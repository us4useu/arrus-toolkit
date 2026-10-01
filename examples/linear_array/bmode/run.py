"""B-mode imaging with a linear array, displayed with GUI4us.

One script for the four transmit schemes of this directory (formerly one GUI4us configuration
directory each):

    classical  focused scan lines, one per element (the classic B-mode)
    pwi        plane wave imaging (32 angles, compounded)
    sta        synthetic transmit aperture (one element at a time)
    dwi        diverging wave imaging (virtual sources behind the probe)

Run it:

    python run.py --cfg /path/to/us4r.prototxt                     # classical, GUI4us window
    python run.py --cfg /path/to/us4r.prototxt --sequence pwi
    python run.py --cfg /path/to/us4r.prototxt --headless          # no window: open the URL in a browser
    python run.py --cfg /path/to/us4r.prototxt --view qt           # the PyQt window
    python run.py --cfg /path/to/us4r.prototxt --view none --capture 10   # no view: save 10 frames

The web view is shown in its own window; it is served on http://127.0.0.1:7777 (--host 0.0.0.0 to
reach it from other machines -- there is no authentication, and the view can change the TX voltage).
--cfg defaults to $ARRUS_SESSION_CFG, else ~/us4r.prototxt.

Everything the old env.py / display.py / app.py files did is here: create_environment (env.py),
create_display (display.py), create_gui (app.py + wiring). The notebook run.ipynb imports them.

Requirements: ARRUS (e.g. the release wheel for your platform) and gui4us.
"""
import argparse
import os
import signal
import sys
from typing import Optional, Tuple

import numpy as np

import arrus
import arrus.logging
import arrus.medium
import arrus.ops.us4r
from arrus.ops.imaging import LinSequence, PwiSequence, StaSequence
from arrus.ops.us4r import Pulse
from arrus.utils.imaging import (
    BandpassFilter, Decimation, EnvelopeDetection, Lambda, LogCompression, Mean, Pipeline,
    QuadratureDemodulation, ReconstructLri, RemapToLogicalOrder, RxBeamforming, ScanConversion, Squeeze,
    Transpose,
)

from gui4us import AppCfg, Gui4us
from gui4us.cfg import Display2D, Layer2D, ViewCfg
from gui4us.model import StreamDataId
from gui4us.model.envs.arrus import ArrusEnvConfiguration, Curve, UltrasoundEnv, get_depth_range

# The B-mode operations (compounding.py, speckle.py; the same as in ../color_doppler) live next to this script.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from compounding import compounding_steps  # noqa: E402
from speckle import Srad  # noqa: E402

SEQUENCES = ("classical", "pwi", "sta", "dwi")

#: Compounding of the beamformed transmits (pwi, sta, dwi; see compounding.py): split into n_incoherent
#: groups of n_coherent; None means "auto" (whatever the other count leaves). Default: all summed on IQ.
N_COHERENT, N_INCOHERENT = None, 1
#: SRAD speckle filter on the envelope (see speckle.py); the settings of holohub's ultrasound_guidance.
SRAD_SETTINGS = dict(iterations=20, step=0.2, rho=0.0, q0=0.5)

#: The B-mode dynamic range [dB] (the pipeline's LogCompression output is absolute dB).
BMODE_DRANGE = (20, 80)


def create_sequence(name: str, probe_model, speed_of_sound: float, z_grid: np.ndarray):
    """The TX/RX sequence of the given transmit scheme."""
    n_elements = probe_model.n_elements
    depth_range = get_depth_range(z_grid)
    pulse = Pulse(center_frequency=8e6, n_periods=1, inverse=False)
    if name == "classical":
        return LinSequence(
            tx_aperture_center_element=np.arange(32, n_elements - 32),
            tx_aperture_size=64,
            tx_focus=20e-3,
            pulse=pulse,
            rx_aperture_center_element=np.arange(32, n_elements - 32),
            rx_aperture_size=64,
            rx_depth_range=depth_range,
            pri=200e-6,
            speed_of_sound=speed_of_sound)
    if name == "pwi":
        return PwiSequence(
            angles=np.linspace(-10, 10, 32)*np.pi/180,
            pulse=pulse,
            rx_depth_range=depth_range,
            speed_of_sound=speed_of_sound,
            pri=200e-6)
    if name == "sta":
        return StaSequence(
            tx_aperture_center_element=np.arange(0, n_elements),
            tx_aperture_size=1,
            tx_focus=0,
            pulse=pulse,
            rx_depth_range=depth_range,
            speed_of_sound=speed_of_sound,
            pri=200e-6)
    if name == "dwi":
        return StaSequence(
            tx_aperture_center=np.linspace(-15, 15, 11)*1e-3,
            tx_aperture_size=32,
            tx_focus=-6e-3,  # a virtual source behind the probe: a diverging wave
            pulse=pulse,
            rx_depth_range=depth_range,
            speed_of_sound=speed_of_sound,
            pri=200e-6)
    raise ValueError(f"Unknown sequence {name!r}; available: {', '.join(SEQUENCES)}")


def imaging_grid(probe_model) -> Tuple[np.ndarray, np.ndarray]:
    """The (x, z) reconstruction grid [m]: the probe's width, 0-40 mm deep, 0.1 mm pixels."""
    x_grid = np.arange(probe_model.x_min, probe_model.x_max, 0.1e-3)
    z_grid = np.arange(0e-3, 40e-3, 0.1e-3)
    return x_grid, z_grid


def speckle_steps(srad_settings: Optional[dict], frame_shape: Tuple[int, int]) -> tuple:
    """The SRAD speckle filter as pipeline steps (on the envelope, before the log compression), or nothing."""
    if not srad_settings:
        return ()
    srad = Srad(**srad_settings)
    # Compile the kernels now: compiling them on the first frame takes long enough to stop the acquisition.
    srad.warm_up(frame_shape)
    return (Lambda(srad),)


def create_pipeline(sequence, x_grid: np.ndarray, z_grid: np.ndarray, placement: str = "/GPU:0",
                    n_coherent: Optional[int] = N_COHERENT, n_incoherent: Optional[int] = N_INCOHERENT,
                    srad_settings: Optional[dict] = SRAD_SETTINGS) -> Pipeline:
    """The B-mode pipeline (the same B-mode processing as in ../color_doppler).

    pwi, sta, dwi: synthetic aperture -- ReconstructLri, then coherent/incoherent compounding of the transmits;
    classical: scan-line beamforming (no compounding). Both: SRAD on the envelope, then log compression.
    """
    frame_shape = (len(z_grid), len(x_grid))
    preprocessing = (
        RemapToLogicalOrder(),
        Transpose(axes=(0, 1, 3, 2)),
        BandpassFilter(),
        QuadratureDemodulation(),
        Decimation(decimation_factor=4, cic_order=2),
    )
    if isinstance(sequence, LinSequence):
        steps = (
            *preprocessing,
            RxBeamforming(),
            EnvelopeDetection(),
            Transpose(axes=(0, 2, 1)),
            ScanConversion(x_grid, z_grid),
            Mean(axis=0),
            *speckle_steps(srad_settings, frame_shape),
            LogCompression(),
        )
    else:
        steps = (
            *preprocessing,
            ReconstructLri(x_grid=x_grid, z_grid=z_grid),
            *compounding_steps(n_coherent, n_incoherent),
            Squeeze(),
            Transpose(),
            *speckle_steps(srad_settings, frame_shape),
            LogCompression(),
        )
    return Pipeline(steps=steps, placement=placement)


def create_environment(session_cfg: str, sequence: str = "classical", voltage: int = 5,
                       n_coherent: Optional[int] = N_COHERENT, n_incoherent: Optional[int] = N_INCOHERENT,
                       srad_settings: Optional[dict] = SRAD_SETTINGS):
    """The GUI4us ARRUS environment (what env.py used to provide), as a factory.

    GUI4us calls the factory on its own environment thread, which then owns the us4R session.
    """
    if sequence not in SEQUENCES:
        raise ValueError(f"Unknown sequence {sequence!r}; available: {', '.join(SEQUENCES)}")

    def configure(session: arrus.Session) -> ArrusEnvConfiguration:
        medium = arrus.medium.Medium(name="ats549", speed_of_sound=1450)
        probe_model = session.get_device("/Us4R:0").get_probe_model()
        x_grid, z_grid = imaging_grid(probe_model)
        tx_rx_sequence = create_sequence(sequence, probe_model, medium.speed_of_sound, z_grid)
        return ArrusEnvConfiguration(
            medium=medium,
            scheme=arrus.ops.us4r.Scheme(
                tx_rx_sequence=tx_rx_sequence,
                processing=create_pipeline(tx_rx_sequence, x_grid, z_grid, n_coherent=n_coherent,
                                           n_incoherent=n_incoherent, srad_settings=srad_settings),
            ),
            # The initial TGC curve; adjustable from the control panel.
            tgc=Curve(points=np.linspace(np.min(z_grid), np.max(z_grid), 10),
                      values=np.linspace(14, 54, 10)),
            voltage=voltage,
        )

    return lambda: UltrasoundEnv(session_cfg=session_cfg, configure=configure)


def create_display(sequence: str = "classical") -> ViewCfg:
    """The displays (what display.py used to provide).

    The extents (hence the OX/OZ axes in mm) come from the reconstruction grid, which ARRUS
    reports in the output metadata.
    """
    return ViewCfg(displays={
        "B-mode": Display2D(
            title=f"B-mode ({sequence})",
            layers=(Layer2D(input=StreamDataId("default", 0), cmap="gray",
                            value_range=BMODE_DRANGE), ),
            ax_labels=("OZ", "OX"),
        ),
    })


def create_gui(session_cfg: str, sequence: str = "classical", voltage: int = 5,
               view: str = "web", host: str = "127.0.0.1", port: int = 7777,
               n_coherent: Optional[int] = N_COHERENT, n_incoherent: Optional[int] = N_INCOHERENT,
               srad_settings: Optional[dict] = SRAD_SETTINGS) -> Gui4us:
    """The GUI4us instance: environment + displays + application settings (app.py)."""
    return Gui4us(
        env=create_environment(session_cfg, sequence=sequence, voltage=voltage, n_coherent=n_coherent,
                               n_incoherent=n_incoherent, srad_settings=srad_settings),
        display=create_display(sequence),
        app=AppCfg(capture_buffer_size=100, title=f"B-mode ({sequence})",
                   view=None if view == "none" else view, host=host, port=port),
    )


def _count_or_auto(value: str) -> Optional[int]:
    return None if value == "auto" else int(value)


#: How long --capture waits for its frames [s].
CAPTURE_TIMEOUT = 60


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cfg", default=os.environ.get("ARRUS_SESSION_CFG", "~/us4r.prototxt"),
                        help="ARRUS session configuration file (default: $ARRUS_SESSION_CFG, "
                             "else ~/us4r.prototxt)")
    parser.add_argument("--sequence", default="classical", choices=SEQUENCES)
    parser.add_argument("--voltage", type=int, default=5, help="TX voltage [V]")
    parser.add_argument("--n-coherent", type=_count_or_auto, default=N_COHERENT, metavar="N|auto",
                        help="pwi/sta/dwi: transmits summed on IQ (coherent compounding); default: auto")
    parser.add_argument("--n-incoherent", type=_count_or_auto, default=N_INCOHERENT, metavar="N|auto",
                        help="pwi/sta/dwi: coherent sums averaged after envelope detection; default: 1 "
                             "(all coherent). Fully incoherent: --n-coherent 1 --n-incoherent auto")
    parser.add_argument("--no-srad", action="store_true", help="no SRAD speckle filter")
    parser.add_argument("--view", default="web", choices=("web", "qt", "none"))
    parser.add_argument("--headless", action="store_true",
                        help="web view: do not open a window, only serve the view (open the URL in a browser)")
    parser.add_argument("--host", default="127.0.0.1", help="browser view: address to bind")
    parser.add_argument("--port", type=int, default=7777, help="browser view: port")
    parser.add_argument("--capture", type=int, default=0, metavar="N",
                        help="capture N frames right after starting and save them to bmode.npy")
    args = parser.parse_args()
    args.cfg = os.path.expanduser(args.cfg)
    if not os.path.exists(args.cfg):
        parser.error(f"session configuration not found: {args.cfg} (use --cfg)")

    # SIGTERM (e.g. `kill`) -> KeyboardInterrupt, so that the `with` block below closes the us4R session;
    # a process killed with the acquisition running leaves the us4R streaming data into the freed memory.
    def _on_sigterm(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, _on_sigterm)

    arrus.set_clog_level(arrus.logging.INFO)
    with create_gui(args.cfg, sequence=args.sequence, voltage=args.voltage, view=args.view,
                    host=args.host, port=args.port, n_coherent=args.n_coherent,
                    n_incoherent=args.n_incoherent,
                    srad_settings=None if args.no_srad else SRAD_SETTINGS) as gui:
        gui.start()
        if args.capture:
            frames = gui.capture(args.capture, timeout=CAPTURE_TIMEOUT)
            if len(frames) < args.capture:
                raise SystemExit(
                    f"Only {len(frames)} of {args.capture} frames arrived within {CAPTURE_TIMEOUT} s. "
                    f"If the log says the us4R watchdog stopped the device, the host did not "
                    f"process the data in time (e.g. GPU kernels compiled on the first frame).")
            data = gui.captured_array(output=0)
            np.save("bmode.npy", data)
            print(f"captured {data.shape} {data.dtype} -> bmode.npy")
        # Blocks until the window is closed (or Ctrl+C with --headless); returns at once for none.
        gui.run(**({"headless": True} if args.headless and args.view == "web" else {}))


if __name__ == "__main__":
    main()
