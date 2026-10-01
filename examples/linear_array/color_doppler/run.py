"""Colour and power Doppler with a linear array, displayed with GUI4us.

Two TX/RX sequences are uploaded (a list of sequences passed to the Scheme, as in the us4r-vandv
validation_doppler test):

    BmodeSequence    21 plane waves, -10..10 deg, 0.5-cycle pulses
    DopplerSequence  32 plane waves at 10 deg, 16-cycle pulses

and processed by a Graph with one pipeline per sequence: "Bmode" (compounded B-mode, normalized to its
maximum) and "Doppler" (the colour and the power Doppler estimates of the Doppler ensemble, NaN outside
their dynamic ranges). The displays overlay the Doppler estimates on the B-mode: a NaN pixel of an
overlay layer is transparent, so the B-mode shows through wherever there is no flow.

Run it:

    python run.py --cfg /path/to/us4r.prototxt              # GUI4us window
    python run.py --cfg /path/to/us4r.prototxt --headless   # no window: open http://127.0.0.1:7777
    python run.py --cfg /path/to/us4r.prototxt --view qt
    python run.py --cfg /path/to/us4r.prototxt --view none --capture 5

The Doppler thresholds (power_dr_min/max [dB], color_dr_min/max [m/s]) are pipeline parameters,
so they appear in the control panel next to the voltage and the TGC.

This script replaces the former env.py / display.py / app.py configuration directory; the custom
operations stay in ops.py and doppler.cc. The notebook run.ipynb imports this script.

Requirements: ARRUS (e.g. the release wheel for your platform), cupy, scipy and gui4us.
"""
import argparse
import os
import signal
import sys
from typing import Optional, Tuple

import cupy as cp
import numpy as np
import scipy.signal

import arrus
import arrus.logging
import arrus.medium
import arrus.ops.us4r
from arrus.ops.us4r import Aperture, Pulse, Rx, Tx, TxRx, TxRxSequence
from arrus.utils.imaging import (
    Decimation, EnvelopeDetection, FirFilter, Graph, Lambda, LogCompression, Mean, Pipeline,
    Processing, QuadratureDemodulation, ReconstructLri, RemapToLogicalOrder, Squeeze, Transpose,
)

from gui4us import AppCfg, Gui4us
from gui4us.cfg import Display2D, DisplayLocation, GridSpec, Layer2D, ViewCfg
from gui4us.model import StreamDataId
from gui4us.model.envs.arrus import ArrusEnvConfiguration, Curve, UltrasoundEnv


# The custom operations (ops.py, doppler.cc) live next to this script.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from ops import (  # noqa: E402
    CreateDopplerFrame, FilterWallClutter, Persistence, ReconstructDoppler, SvdClutterFilter
)
from compounding import compounding_steps  # noqa: E402
from speckle import Srad  # noqa: E402

#: The sequence names (the Graph connects each pipeline to its sequence by name).
BMODE_SEQUENCE, DOPPLER_SEQUENCE = "BmodeSequence", "DopplerSequence"

#: Dynamic ranges: the B-mode [dB, relative to the frame maximum], the Doppler velocity [m/s] (also
#: the colour map range), the Doppler power [dB].
BMODE_DRANGE = (35, 80)
COLOR_DRANGE = (-250e-3, 250e-3)
COLOR_RANGE = COLOR_DRANGE
POWER_DRANGE = (30, 80)

#: The Graph outputs: Output:0 -- the B-mode, Output:1 and Output:2 -- the colour and the power Doppler.
BMODE_OUTPUT, COLOR_OUTPUT, POWER_OUTPUT = 0, 1, 2

#: The wall (clutter) filter: "butter" -- IIR high-pass along the ensemble (normalized cut-off frequency,
#: order), or "svd" -- SVD clutter filter (the number of the removed strongest/weakest components).
CLUTTER_FILTER = "butter"
WALL_FILTER_WN, WALL_FILTER_N = 0.1, 4
SVD_N_TISSUE, SVD_N_NOISE = 3, 0
#: Persistence: the Doppler estimates are averaged over this many last frames (1: no persistence).
PERSISTENCE = 5
DECIMATION_FACTOR = 4
CENTER_FREQUENCY = 6e6  # [Hz]

n_samples = 2*1024+512

#: B-mode compounding (see compounding.py): the transmits are split into n_incoherent groups of n_coherent;
#: None means "auto" (whatever the other count leaves). Default: all summed on IQ (coherent).
N_COHERENT, N_INCOHERENT = None, 1
#: SRAD speckle filter on the B-mode envelope (see speckle.py); the settings of holohub's ultrasound_guidance.
SRAD_SETTINGS = dict(iterations=20, step=0.2, rho=0.0, q0=0.5)


def create_pwi_sequence(angles: np.ndarray, n_periods: float, center_frequency: float,
                    speed_of_sound: float, sample_range: Tuple[int, int], pri: float,
                    name: str) -> TxRxSequence:
    """Plane waves transmitted and received with the whole probe, one per angle."""
    rx = Rx(aperture=Aperture(center=0.0), sample_range=sample_range, downsampling_factor=1)
    ops = []
    for angle in angles:
        tx = Tx(aperture=Aperture(center=0.0),
                excitation=Pulse(center_frequency=center_frequency, n_periods=n_periods,
                                 inverse=False),
                focus=np.inf, angle=angle, speed_of_sound=speed_of_sound)
        ops.append(TxRx(tx, rx, pri))
    return TxRxSequence(ops, name=name)


def create_sta_sequence(n_periods: float, center_frequency: float,
                    speed_of_sound: float, sample_range: Tuple[int, int], pri: float,
                    name: str) -> TxRxSequence:
    """Plane waves transmitted and received with the whole probe, one per angle."""
    rx = Rx(aperture=Aperture(center=0.0), sample_range=sample_range, downsampling_factor=1)
    ops = []
    for i in range(0, 192, 2):
        tx = Tx(aperture=Aperture(center_element=i, size=2),
                excitation=Pulse(center_frequency=center_frequency, n_periods=n_periods,
                                 inverse=False),
                focus=0, angle=0, speed_of_sound=speed_of_sound)
        ops.append(TxRx(tx, rx, pri))
    return TxRxSequence(ops, name=name)



def create_bmode_sequence(speed_of_sound: float) -> TxRxSequence:
    return create_sta_sequence(n_periods=0.5,
                           center_frequency=8e6, speed_of_sound=speed_of_sound,
                           sample_range=(0, n_samples), pri=200e-6, name=BMODE_SEQUENCE)


def create_doppler_sequence(speed_of_sound: float) -> TxRxSequence:
    """The Doppler ensemble: 32 plane waves at 10 deg, 16-cycle pulses."""
    return create_pwi_sequence(angles=np.tile(10*np.pi/180, 32), n_periods=16,
                           center_frequency=CENTER_FREQUENCY, speed_of_sound=speed_of_sound,
                           sample_range=(0, n_samples), pri=200e-6, name=DOPPLER_SEQUENCE)


def speckle_steps(srad_settings: Optional[dict], frame_shape: Tuple[int, int]) -> tuple:
    """The SRAD speckle filter as pipeline steps (on the envelope, before the log compression), or nothing."""
    if not srad_settings:
        return ()
    srad = Srad(**srad_settings)
    # Compile the kernels now: compiling them on the first frame takes long enough to stop the acquisition.
    srad.warm_up(frame_shape)
    return (Lambda(srad),)


def create_clutter_filter(clutter_filter: str = CLUTTER_FILTER, svd_n_tissue: int = SVD_N_TISSUE,
                          svd_n_noise: int = SVD_N_NOISE):
    """The wall (clutter) filter of the Doppler ensemble."""
    if clutter_filter == "butter":
        return FilterWallClutter(wn=WALL_FILTER_WN, n=WALL_FILTER_N, ftype="butter", btype="highpass")
    if clutter_filter == "svd":
        return SvdClutterFilter(n_tissue=svd_n_tissue, n_noise=svd_n_noise)
    raise ValueError(f"Unknown clutter filter: {clutter_filter} (available: butter, svd)")


def create_processing(fs: float, x_grid: np.ndarray, z_grid: np.ndarray,
                      placement: str = "/GPU:0", n_coherent: Optional[int] = N_COHERENT,
                      n_incoherent: Optional[int] = N_INCOHERENT,
                      srad_settings: Optional[dict] = SRAD_SETTINGS, clutter_filter: str = CLUTTER_FILTER,
                      svd_n_tissue: int = SVD_N_TISSUE, svd_n_noise: int = SVD_N_NOISE,
                      persistence: int = PERSISTENCE) -> Processing:
    """The Graph: the "Bmode" pipeline for BmodeSequence, the "Doppler" pipeline for DopplerSequence.

    :param n_coherent: B-mode: how many transmits are summed on IQ (None: auto)
    :param n_incoherent: B-mode: how many of the coherent sums are averaged after envelope detection (None: auto)
    :param srad_settings: B-mode: the SRAD speckle filter settings (see speckle.Srad); None: no filter
    :param clutter_filter: Doppler: the wall filter, "butter" or "svd" (see create_clutter_filter)
    :param persistence: Doppler: the number of the last frames the estimates are averaged over
    """
    fir_taps = scipy.signal.firwin(numtaps=64, cutoff=np.array([0.5, 1.5])*CENTER_FREQUENCY,
                                   pass_zero="bandpass", fs=fs)
    bmode_pipeline = Pipeline(
        steps=(
            RemapToLogicalOrder(),
            Transpose(axes=(0, 1, 3, 2)),
            FirFilter(taps=fir_taps),
            QuadratureDemodulation(),
            Decimation(decimation_factor=DECIMATION_FACTOR, cic_order=2),
            ReconstructLri(x_grid=x_grid, z_grid=z_grid),
            # Coherent/incoherent compounding -> the envelope (x, z).
            *compounding_steps(n_coherent, n_incoherent),
            Squeeze(),
            Transpose(),
            # Speckle reduction on the envelope, (z, x).
            *speckle_steps(srad_settings, (len(z_grid), len(x_grid))),
            LogCompression(),
        ),
        placement=placement,
        name="Bmode",
    )
    doppler_pipeline = Pipeline(
        steps=(
            RemapToLogicalOrder(),
            Transpose(axes=(0, 1, 3, 2)),
            FirFilter(taps=fir_taps),
            QuadratureDemodulation(),
            Decimation(decimation_factor=DECIMATION_FACTOR, cic_order=2),
            ReconstructLri(x_grid=x_grid, z_grid=z_grid),
            Squeeze(),
            create_clutter_filter(clutter_filter, svd_n_tissue, svd_n_noise),
            ReconstructDoppler(),
            # The colour and the power estimates averaged over the last frames.
            Persistence(n_frames=persistence),
            Pipeline(
                # -> Output:1 of "Doppler": the power Doppler.
                steps=(
                    CreateDopplerFrame(color_dynamic_range=COLOR_DRANGE,
                                       power_dynamic_range=POWER_DRANGE, frame_type="power"),
                    Transpose(),
                ),
                placement=placement,
            ),
            # -> Output:0 of "Doppler": the colour Doppler.
            CreateDopplerFrame(color_dynamic_range=COLOR_DRANGE,
                               power_dynamic_range=POWER_DRANGE, frame_type="color"),
            Transpose(),
        ),
        placement=placement,
        name="Doppler",
    )
    graph = Graph(
        operations={bmode_pipeline, doppler_pipeline},
        dependencies={
            bmode_pipeline.name: BMODE_SEQUENCE,
            doppler_pipeline.name: DOPPLER_SEQUENCE,
            f"Output:{BMODE_OUTPUT}": f"{bmode_pipeline.name}/Output:0",
            f"Output:{COLOR_OUTPUT}": f"{doppler_pipeline.name}/Output:0",
            f"Output:{POWER_OUTPUT}": f"{doppler_pipeline.name}/Output:1",
        },
    )
    # The environment sets the Processing callback.
    return Processing(graph=graph)


def create_environment(session_cfg: str, voltage: int = 10, n_coherent: Optional[int] = N_COHERENT,
                       n_incoherent: Optional[int] = N_INCOHERENT,
                       srad_settings: Optional[dict] = SRAD_SETTINGS, **doppler):
    """The GUI4us ARRUS environment (what env.py used to provide), as a factory.

    :param doppler: the Doppler processing settings: clutter_filter, svd_n_tissue, svd_n_noise, persistence
      (see create_processing)
    """

    def configure(session: arrus.Session) -> ArrusEnvConfiguration:
        medium = arrus.medium.Medium(name="human_carotid", speed_of_sound=1540)
        us4r = session.get_device("/Us4R:0")
        probe_model = us4r.get_probe_model()
        x_grid = np.arange(probe_model.x_min, probe_model.x_max, 0.1e-3)
        z_grid = np.arange(5e-3, 30e-3, 0.1e-3)
        bmode_sequence = create_bmode_sequence(medium.speed_of_sound)
        doppler_sequence = create_doppler_sequence(medium.speed_of_sound)
        return ArrusEnvConfiguration(
            medium=medium,
            scheme=arrus.ops.us4r.Scheme(
                tx_rx_sequence=[bmode_sequence, doppler_sequence],
                processing=create_processing(us4r.current_sampling_frequency, x_grid, z_grid,
                                             n_coherent=n_coherent, n_incoherent=n_incoherent,
                                             srad_settings=srad_settings, **doppler),
            ),
            tgc=Curve(points=np.linspace(np.min(z_grid), np.max(z_grid), 10),
                      values=np.linspace(34, 54, 10)),
            voltage=voltage,
        )

    return lambda: UltrasoundEnv(session_cfg=session_cfg, configure=configure)


def create_display() -> ViewCfg:
    """Two displays, one above the other: colour Doppler and power Doppler, each over the B-mode.

    The second layer of each display is an overlay: its NaN pixels are transparent.
    """
    bmode = Layer2D(input=StreamDataId("default", BMODE_OUTPUT), cmap="gray",
                    value_range=BMODE_DRANGE)
    return ViewCfg(
        displays={
            "ColorDoppler": Display2D(
                title="Color Doppler",
                layers=[bmode, Layer2D(input=StreamDataId("default", COLOR_OUTPUT), cmap="bwr",
                                        value_range=COLOR_RANGE)],
                ax_labels=("OZ", "OX"),
            ),
            "PowerDoppler": Display2D(
                title="Power Doppler",
                layers=(bmode, Layer2D(input=StreamDataId("default", POWER_OUTPUT), cmap="hot",
                                       value_range=POWER_DRANGE)),
                ax_labels=("OZ", "OX"),
            ),
        },
        grid_spec=GridSpec(n_rows=2, n_columns=1, locations=(
            DisplayLocation(rows=0, columns=0, display_id="ColorDoppler"),
            DisplayLocation(rows=1, columns=0, display_id="PowerDoppler"),
        )),
    )


def create_gui(session_cfg: str, voltage: int = 10, view: str = "web",
               host: str = "127.0.0.1", port: int = 7777, n_coherent: Optional[int] = N_COHERENT,
               n_incoherent: Optional[int] = N_INCOHERENT,
               srad_settings: Optional[dict] = SRAD_SETTINGS, **doppler) -> Gui4us:
    """The GUI4us instance: environment + displays + application settings (app.py)."""
    return Gui4us(
        env=create_environment(session_cfg, voltage=voltage, n_coherent=n_coherent,
                               n_incoherent=n_incoherent, srad_settings=srad_settings, **doppler),
        display=create_display(),
        app=AppCfg(capture_buffer_size=10, title="Color Doppler",
                   view=None if view == "none" else view, host=host, port=port),
    )


def _count_or_auto(value: str) -> Optional[int]:
    return None if value == "auto" else int(value)


#: How long --capture waits for its frames [s].
CAPTURE_TIMEOUT = 60


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cfg", default=os.environ.get("ARRUS_SESSION_CFG",
                                                        os.path.join(HERE, "us4r.prototxt")),
                        help="ARRUS session configuration file (default: $ARRUS_SESSION_CFG, "
                             "else us4r.prototxt next to this script)")
    parser.add_argument("--voltage", type=int, default=10, help="TX voltage [V]")
    parser.add_argument("--view", default="web", choices=("web", "qt", "none"))
    parser.add_argument("--headless", action="store_true",
                        help="web view: do not open a window, only serve the view (open the URL in a browser)")
    parser.add_argument("--host", default="127.0.0.1", help="web view: address to bind")
    parser.add_argument("--port", type=int, default=7777, help="web view: port")
    parser.add_argument("--n-coherent", type=_count_or_auto, default=N_COHERENT, metavar="N|auto",
                        help="B-mode: transmits summed on IQ (coherent compounding); default: auto")
    parser.add_argument("--n-incoherent", type=_count_or_auto, default=N_INCOHERENT, metavar="N|auto",
                        help="B-mode: coherent sums averaged after envelope detection; default: 1 "
                             "(all coherent). Fully incoherent: --n-coherent 1 --n-incoherent auto")
    parser.add_argument("--no-srad", action="store_true", help="B-mode: no SRAD speckle filter")
    parser.add_argument("--clutter-filter", default=CLUTTER_FILTER, choices=("butter", "svd"),
                        help="Doppler: the wall filter: butter (IIR high-pass) or svd (SVD clutter filter)")
    parser.add_argument("--svd-tissue", type=int, default=SVD_N_TISSUE,
                        help="Doppler, --clutter-filter svd: the number of the removed strongest components")
    parser.add_argument("--svd-noise", type=int, default=SVD_N_NOISE,
                        help="Doppler, --clutter-filter svd: the number of the removed weakest components")
    parser.add_argument("--persistence", type=int, default=PERSISTENCE,
                        help="Doppler: average the estimates over this many last frames (1: off)")
    parser.add_argument("--capture", type=int, default=0, metavar="N",
                        help="capture N frames right after starting and save them to doppler.npz")
    args = parser.parse_args()
    args.cfg = os.path.expanduser(args.cfg)
    if not os.path.exists(args.cfg):
        parser.error(f"session configuration not found: {args.cfg} (use --cfg)")

    # SIGTERM (e.g. `kill`) -> KeyboardInterrupt, so that the `with` block below closes the us4R session;
    # a process killed with the acquisition running leaves the us4R streaming data into the freed memory.
    def _on_sigterm(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, _on_sigterm)

    arrus.set_clog_level(arrus.logging.TRACE)
    with create_gui(args.cfg, voltage=args.voltage, view=args.view, host=args.host, port=args.port,
                    n_coherent=args.n_coherent, n_incoherent=args.n_incoherent,
                    srad_settings=None if args.no_srad else SRAD_SETTINGS,
                    clutter_filter=args.clutter_filter, svd_n_tissue=args.svd_tissue,
                    svd_n_noise=args.svd_noise, persistence=args.persistence) as gui:
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
        # Blocks until the window is closed (or Ctrl+C with --headless); returns at once for none.
        gui.run(**({"headless": True} if args.headless and args.view == "web" else {}))


if __name__ == "__main__":
    main()
