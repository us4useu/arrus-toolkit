"""Colour and power Doppler with a linear array, displayed with GUI4us.

The sequence interleaves 64 Doppler plane waves (a fixed 10 deg angle, 16-cycle pulses) with 7
B-mode plane waves (-10..10 deg). The pipeline reconstructs both, estimates the Doppler
velocity and power from the Doppler ensemble, and masks the Doppler estimates outside their
dynamic ranges with NaN. The displays overlay them on the B-mode: a NaN pixel of an overlay
layer is transparent, so the B-mode shows through wherever there is no flow.

Run it:

    python run.py --cfg /path/to/us4r.prototxt              # browser view, http://127.0.0.1:7777
    python run.py --cfg /path/to/us4r.prototxt --view qt
    python run.py --cfg /path/to/us4r.prototxt --view none --capture 5

The Doppler thresholds (power_dr_min/max [dB], color_dr_min/max [rad]) are pipeline parameters,
so they appear in the control panel next to the voltage and the TGC.

This script replaces the former env.py / display.py / app.py configuration directory; the custom
operations stay in ops.py and doppler.cc. The notebook run.ipynb imports this script.

Requirements: ARRUS (e.g. the release wheel for your platform), cupy, scipy and gui4us.
"""
import argparse
import os
import sys
from typing import Tuple

import numpy as np
import scipy.signal

import arrus
import arrus.logging
import arrus.medium
import arrus.ops.us4r
from arrus.ops.us4r import Aperture, Pulse, Rx, Tx, TxRx, TxRxSequence
from arrus.utils.imaging import (
    Decimation, EnvelopeDetection, FirFilter, LogCompression, Mean, Output, Pipeline,
    QuadratureDemodulation, ReconstructLri, RemapToLogicalOrder, SelectFrames, Squeeze, Transpose,
)

from gui4us import AppCfg, Gui4us
from gui4us.cfg import Display2D, DisplayLocation, GridSpec, Layer2D, ViewCfg
from gui4us.model import StreamDataId
from gui4us.model.envs.arrus import ArrusEnvConfiguration, Curve, UltrasoundEnv

# The custom operations (ops.py, doppler.cc) live next to this script.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from ops import CreateDopplerFrame, FilterWallClutter, ReconstructDoppler  # noqa: E402

#: Dynamic ranges: the B-mode [dB], the Doppler phase shift [rad], the Doppler power [dB].
BMODE_DRANGE = (70, 120)
COLOR_DRANGE = np.array((0, np.pi/2))
POWER_DRANGE = (50, 80)
#: The colour map range of the velocity display: symmetric around 0.
COLOR_RANGE = tuple(np.array([-1, 1])*np.abs(COLOR_DRANGE).max()/2)

#: The stream outputs of the pipeline (see create_environment).
BMODE_OUTPUT, COLOR_OUTPUT, POWER_OUTPUT = 0, 1, 2


def create_sequence(angles: np.ndarray, n_periods: float, center_frequency: float,
                    speed_of_sound: float, sample_range: Tuple[int, int], pri: float) -> TxRxSequence:
    """Plane waves transmitted and received with the whole probe, one per angle."""
    rx = Rx(aperture=Aperture(center=0.0), sample_range=sample_range, downsampling_factor=1)
    ops = []
    for angle in angles:
        tx = Tx(aperture=Aperture(center=0.0),
                excitation=Pulse(center_frequency=center_frequency, n_periods=n_periods,
                                 inverse=False),
                focus=np.inf, angle=angle, speed_of_sound=speed_of_sound)
        ops.append(TxRx(tx, rx, pri))
    return TxRxSequence(ops)


def create_environment(session_cfg: str, voltage: float = 10, n_tx_doppler: int = 64,
                       n_tx_bmode: int = 7, doppler_angle: float = 10):
    """The GUI4us ARRUS environment (what env.py used to provide), as a factory."""

    def configure(session: arrus.Session) -> ArrusEnvConfiguration:
        medium = arrus.medium.Medium(name="human_carotid", speed_of_sound=1540)
        us4r = session.get_device("/Us4R:0")
        probe_model = us4r.get_probe_model()
        x_grid = np.arange(probe_model.x_min, probe_model.x_max, 0.1e-3)
        z_grid = np.arange(0e-3, 25e-3, 0.1e-3)
        fs = us4r.current_sampling_frequency
        center_frequency = 6e6
        pri = 100e-6
        sample_range = (0, 4*512)
        fir_taps = scipy.signal.firwin(numtaps=64, cutoff=np.array([0.5, 1.5])*center_frequency,
                                       pass_zero="bandpass", fs=fs)
        doppler_sequence = create_sequence(
            angles=np.tile(doppler_angle*np.pi/180, n_tx_doppler), n_periods=16,
            center_frequency=center_frequency, speed_of_sound=medium.speed_of_sound,
            sample_range=sample_range, pri=pri)
        bmode_sequence = create_sequence(
            angles=np.linspace(-10, 10, n_tx_bmode)*np.pi/180, n_periods=2,
            center_frequency=center_frequency, speed_of_sound=medium.speed_of_sound,
            sample_range=sample_range, pri=pri)
        # NOTE: the order matters -- the pipeline selects the Doppler frames first.
        sequence = TxRxSequence(ops=list(doppler_sequence.ops) + list(bmode_sequence.ops))

        pipeline = Pipeline(
            steps=(
                RemapToLogicalOrder(),
                Transpose(axes=(0, 1, 3, 2)),
                FirFilter(taps=fir_taps),
                QuadratureDemodulation(),
                Decimation(decimation_factor=4, cic_order=2),
                Pipeline(
                    # -> Doppler: the colour (velocity) and the power estimates, NaN outside
                    # their dynamic ranges.
                    steps=(
                        SelectFrames(frames=np.arange(0, n_tx_doppler)),
                        ReconstructLri(x_grid=x_grid, z_grid=z_grid),
                        Output(),
                        Squeeze(),
                        FilterWallClutter(wn=0.5, n=8, ftype="butter", btype="highpass"),
                        ReconstructDoppler(),
                        Pipeline(
                            steps=(
                                CreateDopplerFrame(color_dynamic_range=COLOR_DRANGE,
                                                   power_dynamic_range=POWER_DRANGE,
                                                   frame_type="power"),
                                Transpose(),
                            ),
                            placement="/GPU:0",
                        ),
                        CreateDopplerFrame(color_dynamic_range=COLOR_DRANGE,
                                           power_dynamic_range=POWER_DRANGE,
                                           frame_type="color"),
                        Transpose(),
                    ),
                    placement="/GPU:0",
                ),
                # -> B-mode: the compounded plane waves.
                SelectFrames(frames=np.arange(n_tx_doppler, n_tx_doppler + n_tx_bmode)),
                ReconstructLri(x_grid=x_grid, z_grid=z_grid),
                Mean(axis=1),
                EnvelopeDetection(),
                Mean(axis=0),
                Squeeze(),
                Transpose(),
                LogCompression(),
            ),
            placement="/GPU:0",
        )
        return ArrusEnvConfiguration(
            medium=medium,
            scheme=arrus.ops.us4r.Scheme(tx_rx_sequence=sequence, processing=pipeline),
            tgc=Curve(points=np.linspace(np.min(z_grid), np.max(z_grid), 10),
                      values=np.linspace(34, 54, 10)),
            voltage=voltage,
        )

    return lambda: UltrasoundEnv(session_cfg=session_cfg, configure=configure)


def create_display() -> ViewCfg:
    """Two displays side by side: colour Doppler and power Doppler, each over the B-mode.

    The second layer of each display is an overlay: its NaN pixels are transparent.
    """
    bmode = Layer2D(input=StreamDataId("default", BMODE_OUTPUT), cmap="gray",
                    value_range=BMODE_DRANGE)
    return ViewCfg(
        displays={
            "ColorDoppler": Display2D(
                title="Color Doppler",
                layers=(bmode, Layer2D(input=StreamDataId("default", COLOR_OUTPUT), cmap="bwr",
                                       value_range=COLOR_RANGE)),
                ax_labels=("OZ", "OX"),
            ),
            "PowerDoppler": Display2D(
                title="Power Doppler",
                layers=(bmode, Layer2D(input=StreamDataId("default", POWER_OUTPUT), cmap="hot",
                                       value_range=POWER_DRANGE)),
                ax_labels=("OZ", "OX"),
            ),
        },
        grid_spec=GridSpec(n_rows=1, n_columns=2, locations=(
            DisplayLocation(rows=0, columns=0, display_id="ColorDoppler"),
            DisplayLocation(rows=0, columns=1, display_id="PowerDoppler"),
        )),
    )


def create_gui(session_cfg: str, voltage: float = 10, view: str = "web",
               host: str = "127.0.0.1", port: int = 7777) -> Gui4us:
    """The GUI4us instance: environment + displays + application settings (app.py)."""
    return Gui4us(
        env=create_environment(session_cfg, voltage=voltage),
        display=create_display(),
        app=AppCfg(capture_buffer_size=10, title="Color Doppler",
                   view=None if view == "none" else view, host=host, port=port),
    )


#: How long --capture waits for its frames [s].
CAPTURE_TIMEOUT = 60


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cfg", default=os.environ.get("ARRUS_SESSION_CFG",
                                                        os.path.join(HERE, "us4r.prototxt")),
                        help="ARRUS session configuration file (default: $ARRUS_SESSION_CFG, "
                             "else us4r.prototxt next to this script)")
    parser.add_argument("--voltage", type=float, default=10, help="TX voltage [V]")
    parser.add_argument("--view", default="web", choices=("web", "qt", "none"))
    parser.add_argument("--host", default="127.0.0.1", help="browser view: address to bind")
    parser.add_argument("--port", type=int, default=7777, help="browser view: port")
    parser.add_argument("--capture", type=int, default=0, metavar="N",
                        help="capture N frames right after starting and save them to doppler.npz")
    args = parser.parse_args()
    if not os.path.exists(args.cfg):
        parser.error(f"session configuration not found: {args.cfg} (use --cfg)")

    arrus.set_clog_level(arrus.logging.INFO)
    with create_gui(args.cfg, voltage=args.voltage, view=args.view, host=args.host,
                    port=args.port) as gui:
        gui.start()
        if args.capture:
            frames = gui.capture(args.capture, timeout=CAPTURE_TIMEOUT)
            if len(frames) < args.capture:
                raise SystemExit(
                    f"Only {len(frames)} of {args.capture} frames arrived within {CAPTURE_TIMEOUT} s. "
                    f"If the log says the us4R watchdog stopped the device, the host did not "
                    f"process the data in time (e.g. GPU kernels compiled on the first frame).")
            np.savez("doppler.npz", bmode=gui.captured_array(BMODE_OUTPUT),
                     color=gui.captured_array(COLOR_OUTPUT),
                     power=gui.captured_array(POWER_OUTPUT))
            print(f"captured {args.capture} frames -> doppler.npz")
        gui.run()


if __name__ == "__main__":
    main()
