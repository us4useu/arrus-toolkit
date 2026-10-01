"""Plane-wave needle tracking: the TX angle follows the needle detected in the B-mode image.

Acquisition: a single plane wave per frame (a raw TxRxSequence, as in ARRUS' custom_tx_rx_sequence.py example),
full TX and RX aperture; imaging parameters as in arrus-toolkit's linear_array/bmode/pwi example.

All the TX angles from ``TX_ANGLES`` are uploaded to the us4OEMs as TX delay profiles (scheme constants
``/needle/txAngle:{i}``); switching the angle is ``session.set_parameters({"/Us4R:0/needle/txAngle": i})``,
without re-uploading the sequence.

The closed loop (``NeedleTrackingEnv``, MANUAL work mode, one frame per trigger):

1. apply the selected TX angle: the TX delay profile on the hardware + the same angle in the beamformer,
2. trigger a single acquisition,
3. the pipeline (GPU): reconstruction -> B-mode -> needle detection (Hough transform) -> the detected angle
   (or None) goes into the buffer of angles -> the next TX angle (exploration min->max when the buffer is empty
   or all None, otherwise the most recent detected angle); see needle_tracking.py.

The TX angle switching times are printed in the terminal (the Python call and, from ARRUS, the breakdown with
the us4r-api part).

    gui4us --cfg example/pw_needle_tracking

Environment variables: ARRUS_SESSION_CFG, ARRUS_VOLTAGE, NEEDLE_TX_ANGLE_MIN/MAX/STEP [deg], NEEDLE_BUFFER_SIZE,
NEEDLE_MIN_LINE_ANGLE [deg], NEEDLE_SHOW_ARRUS_TIMING (0/1).
"""
import math
import os
import sys
import threading
import time
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import arrus  # noqa: E402
import arrus.framework  # noqa: E402
import arrus.logging  # noqa: E402
import arrus.medium  # noqa: E402
from arrus.ops.us4r import Scheme, Pulse, Tx, Rx, TxRx, TxRxSequence, Aperture  # noqa: E402
from arrus.utils.imaging import (  # noqa: E402
    Pipeline, Processing, RemapToLogicalOrder, Transpose, BandpassFilter, QuadratureDemodulation, Decimation,
    Mean, EnvelopeDetection, LogCompression, Output
)

from gui4us.model.envs.arrus import ArrusEnvConfiguration, Curve, UltrasoundEnv, get_depth_range  # noqa: E402

import arrus.kernels.tx_rx_sequence  # noqa: E402
if not hasattr(arrus.kernels.tx_rx_sequence, "_sort_tx_delay_constants"):
    raise RuntimeError(
        f"This example needs ARRUS with the runtime TX angle selection (txAngle TX delay profiles); "
        f"the ARRUS in use ({arrus.__file__}) does not support it. Run it with the patched ARRUS build, e.g. "
        f"PYTHONPATH=~/src/needle/arrus/build/api/python gui4us --cfg pw_needle_tracking")

from needle_tracking import NeedleTxAngleSelection, SteeredPwReconstructLri  # noqa: E402
from needle_config import (  # noqa: E402
    SESSION_CFG, VOLTAGE, SPEED_OF_SOUND, CENTER_FREQUENCY, SEQUENCE_NAME, TX_ANGLE_PARAMETER, TX_ANGLES,
    BUFFER_SIZE, HISTORY_SIZE, SHOW_ARRUS_TIMING, DETECTOR_PARAMS
)


class NeedleTrackingEnv(UltrasoundEnv):
    """
    ARRUS environment with the TX angle selected dynamically, frame by frame, by the processing pipeline.

    The scheme runs in the MANUAL work mode: this environment triggers the acquisitions itself (in its own thread),
    one frame at a time, so the TX angle of each frame is known exactly, both by the hardware and the beamformer.
    """

    def __init__(self, session_cfg: str, log_file: str = "arrus.log", log_file_level=arrus.logging.INFO):
        self.reconstruction = None
        self.selection = None
        self._loop_thread = None
        self._running = False
        self._frame_done = threading.Event()
        self._current_profile = None
        self._stats = _SwitchStats()
        super().__init__(session_cfg=session_cfg, configure=self._configure,
                         log_file=log_file, log_file_level=log_file_level)
        print(f"[needle] TX angles: {TX_ANGLES.tolist()} [deg], buffer size: {BUFFER_SIZE}, "
              f"outputs: {[m.input_shape for m in self.metadata]}")

    def _configure(self, session: arrus.Session) -> ArrusEnvConfiguration:
        medium = arrus.medium.Medium(name="ats549", speed_of_sound=SPEED_OF_SOUND)
        us4r = session.get_device("/Us4R:0")
        probe_model = us4r.get_probe_model()
        n_elements = probe_model.n_elements
        # Imaging grid (as in arrus-toolkit linear_array/bmode/pwi).
        x_grid = np.arange(probe_model.x_min, probe_model.x_max, 0.1e-3)
        z_grid = np.arange(0e-3, 40e-3, 0.1e-3)
        tgc_sampling_points = np.linspace(np.min(z_grid), np.max(z_grid), 10)
        tgc_values = np.linspace(14, 54, 10)
        # RX: the number of samples covering the depth range (a multiple of 64).
        fs = us4r.sampling_frequency
        n_samples = 64*int(math.ceil(2*get_depth_range(z_grid)[1]/SPEED_OF_SOUND*fs/64))

        tx_angles = np.deg2rad(TX_ANGLES)
        sequence = TxRxSequence(
            ops=[
                TxRx(
                    # Full TX and RX aperture (as Aperture: required by ReconstructLri).
                    Tx(aperture=Aperture(center=0.0, size=n_elements),
                       excitation=Pulse(center_frequency=CENTER_FREQUENCY, n_periods=2, inverse=False),
                       focus=np.inf,  # plane wave
                       angle=float(tx_angles[0]),  # the default delays: overwritten by the profiles at runtime
                       speed_of_sound=SPEED_OF_SOUND),
                    Rx(aperture=Aperture(center=0.0, size=n_elements),
                       sample_range=(0, n_samples),
                       downsampling_factor=1),
                    pri=200e-6
                ),
            ],
            # TGC: set by UltrasoundEnv after the upload.
            tgc_curve=[],
            name=SEQUENCE_NAME,
        )
        constants = [
            arrus.framework.Constant(value=float(angle), placement="/Us4R:0",
                                     name=f"/{SEQUENCE_NAME}/txAngle:{i}")
            for i, angle in enumerate(tx_angles)
        ]
        self.reconstruction = SteeredPwReconstructLri(x_grid=x_grid, z_grid=z_grid, tx_angles=tx_angles)
        self.selection = NeedleTxAngleSelection(
            x_grid=x_grid, z_grid=z_grid, tx_angles=TX_ANGLES, detector_params=DETECTOR_PARAMS,
            buffer_size=BUFFER_SIZE, history_size=HISTORY_SIZE)
        pipeline = Pipeline(
            steps=(
                RemapToLogicalOrder(),
                Transpose(axes=(0, 1, 3, 2)),
                BandpassFilter(),
                QuadratureDemodulation(),
                Decimation(decimation_factor=4, cic_order=2),
                self.reconstruction,
                Mean(axis=1),  # Along TX axis.
                EnvelopeDetection(),
                Mean(axis=0),
                Transpose(),
                LogCompression(),
                Output(),  # B-mode
                self.selection,  # TX angle history
            ),
            placement="/GPU:0")
        scheme = Scheme(
            tx_rx_sequence=sequence,
            processing=Processing(pipeline, input_name=SEQUENCE_NAME),
            constants=constants,
            work_mode="MANUAL",
        )
        return ArrusEnvConfiguration(
            scheme=scheme,
            tgc=Curve(points=tgc_sampling_points, values=tgc_values),
            medium=medium,
            voltage=VOLTAGE,
        )

    # Acquisition loop.
    def start(self) -> None:
        if self._running:
            return
        if SHOW_ARRUS_TIMING:
            # In the running MANUAL loop, ARRUS logs at DEBUG level only the TX delay profile changes.
            arrus.set_clog_level(arrus.logging.DEBUG)
        self._running = True
        self._is_running = True
        self._loop_thread = threading.Thread(target=self._loop, name="needle-tracking-loop", daemon=True)
        self._loop_thread.start()

    def stop(self) -> None:
        self._running = False
        self._frame_done.set()
        if self._loop_thread is not None and self._loop_thread is not threading.current_thread():
            self._loop_thread.join(timeout=5)
        self._loop_thread = None
        self.session.stop_scheme()
        self._is_running = False
        if SHOW_ARRUS_TIMING:
            arrus.set_clog_level(arrus.logging.INFO)
        self._stats.print_summary(force=True)

    def _loop(self):
        try:
            while self._running:
                tx_angle = self.selection.next_tx_angle
                profile = self.selection.selector.snap_id(tx_angle)
                if profile != self._current_profile:
                    self._apply_profile(profile)
                self.selection.acquisition_tx_angle = float(TX_ANGLES[profile])
                self._frame_done.clear()
                self.session.run(sync=True, timeout=1000)
                if not self._frame_done.wait(timeout=5.0) and self._running:
                    print("[needle] WARNING: no processed frame within 5 s.")
                self._stats.on_frame(self.selection)
        except Exception:
            print("[needle] Acquisition loop failed:")
            traceback.print_exc()
            self._running = False

    def _apply_profile(self, profile: int):
        previous = self._current_profile
        start = time.perf_counter()
        self.session.set_parameters({TX_ANGLE_PARAMETER: profile})
        elapsed = time.perf_counter() - start
        # The beamformer: the same TX angle (no frame is being processed now: the previous one is done).
        self.reconstruction.select_profile(profile)
        self._current_profile = profile
        previous_angle = "default" if previous is None else f"{TX_ANGLES[previous]:+.1f}"
        mode = "exploring" if self.selection.selector.is_exploring else "tracking"
        print(f"[needle] TX angle {previous_angle} -> {TX_ANGLES[profile]:+.1f} deg ({mode}): "
              f"session.set_parameters: {elapsed*1e3:.2f} ms")
        self._stats.on_switch(elapsed)

    def _on_new_data(self, input_elements):
        super()._on_new_data(input_elements)
        self._frame_done.set()


class _SwitchStats:
    """Prints a summary of the frame rate, TX angle switching and detection times every few seconds."""

    def __init__(self, period=5.0):
        self.period = period
        self._reset()

    def _reset(self):
        self._start = time.perf_counter()
        self._n_frames = 0
        self._switch_times = []
        self._detection_times = []
        self._n_detected = 0

    def on_switch(self, elapsed):
        self._switch_times.append(elapsed)

    def on_frame(self, selection: NeedleTxAngleSelection):
        self._n_frames += 1
        self._detection_times.append(selection.last_processing_time)
        self._n_detected += selection.last_detection is not None
        self.print_summary()

    def print_summary(self, force=False):
        elapsed = time.perf_counter() - self._start
        if (elapsed < self.period and not force) or self._n_frames == 0:
            return
        line = (f"[needle] {self._n_frames/elapsed:.1f} frames/s, needle detected in {self._n_detected}/"
                f"{self._n_frames} frames, detection+selection: {np.mean(self._detection_times)*1e3:.2f} ms/frame")
        if self._switch_times:
            t = np.asarray(self._switch_times)*1e3
            line += (f"; TX angle switches: {len(t)}, set_parameters [ms]: mean {t.mean():.2f}, "
                     f"min {t.min():.2f}, max {t.max():.2f}")
        print(line)
        self._reset()


ENV = NeedleTrackingEnv(session_cfg=SESSION_CFG)
