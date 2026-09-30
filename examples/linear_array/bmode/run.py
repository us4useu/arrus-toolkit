"""B-mode imaging with a linear array, displayed with GUI4us.

One script for the four transmit schemes of this directory (formerly one GUI4us configuration
directory each):

    classical  focused scan lines, one per element (the classic B-mode)
    pwi        plane wave imaging (32 angles, compounded)
    sta        synthetic transmit aperture (one element at a time)
    dwi        diverging wave imaging (virtual sources behind the probe)

Run it:

    python run.py --cfg /path/to/us4r.prototxt                     # classical, browser view
    python run.py --cfg /path/to/us4r.prototxt --sequence pwi
    python run.py --cfg /path/to/us4r.prototxt --view qt           # the PyQt window
    python run.py --cfg /path/to/us4r.prototxt --view none --capture 10   # no view: save 10 frames

The browser view is served on http://127.0.0.1:7777 (--host 0.0.0.0 to reach it from other
machines -- there is no authentication, and the view can change the TX voltage).

Everything the old env.py / display.py / app.py files did is here: create_environment (env.py),
create_display (display.py), create_gui (app.py + wiring). The notebook run.ipynb imports them.

Requirements: ARRUS (e.g. the release wheel for your platform) and gui4us.
"""
import argparse
import os
from typing import Tuple

import numpy as np

import arrus
import arrus.logging
import arrus.medium
import arrus.ops.us4r
from arrus.ops.imaging import LinSequence, PwiSequence, StaSequence
from arrus.ops.us4r import Pulse
from arrus.utils.imaging import get_bmode_imaging

from gui4us import AppCfg, Gui4us
from gui4us.cfg import Display2D, Layer2D, ViewCfg
from gui4us.model import StreamDataId
from gui4us.model.envs.arrus import ArrusEnvConfiguration, Curve, UltrasoundEnv, get_depth_range

SEQUENCES = ("classical", "pwi", "sta", "dwi")

#: The B-mode dynamic range [dB] (the pipeline's LogCompression output is absolute dB).
BMODE_DRANGE = (20, 80)


def create_sequence(name: str, probe_model, speed_of_sound: float, z_grid: np.ndarray):
    """The TX/RX sequence of the given transmit scheme."""
    n_elements = probe_model.n_elements
    depth_range = get_depth_range(z_grid)
    pulse = Pulse(center_frequency=6e6, n_periods=2, inverse=False)
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
            tx_aperture_center_element=np.arange(32, n_elements - 32),
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


def create_environment(session_cfg: str, sequence: str = "classical", voltage: float = 5):
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
                processing=get_bmode_imaging(sequence=tx_rx_sequence, grid=(x_grid, z_grid)),
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


def create_gui(session_cfg: str, sequence: str = "classical", voltage: float = 5,
               view: str = "web", host: str = "127.0.0.1", port: int = 7777) -> Gui4us:
    """The GUI4us instance: environment + displays + application settings (app.py)."""
    return Gui4us(
        env=create_environment(session_cfg, sequence=sequence, voltage=voltage),
        display=create_display(sequence),
        app=AppCfg(capture_buffer_size=100, title=f"B-mode ({sequence})",
                   view=None if view == "none" else view, host=host, port=port),
    )


#: How long --capture waits for its frames [s].
CAPTURE_TIMEOUT = 60


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cfg", default=os.environ.get("ARRUS_SESSION_CFG", "us4r.prototxt"),
                        help="ARRUS session configuration file (default: $ARRUS_SESSION_CFG, "
                             "else us4r.prototxt)")
    parser.add_argument("--sequence", default="classical", choices=SEQUENCES)
    parser.add_argument("--voltage", type=float, default=5, help="TX voltage [V]")
    parser.add_argument("--view", default="web", choices=("web", "qt", "none"))
    parser.add_argument("--host", default="127.0.0.1", help="browser view: address to bind")
    parser.add_argument("--port", type=int, default=7777, help="browser view: port")
    parser.add_argument("--capture", type=int, default=0, metavar="N",
                        help="capture N frames right after starting and save them to bmode.npy")
    args = parser.parse_args()
    if not os.path.exists(args.cfg):
        parser.error(f"session configuration not found: {args.cfg} (use --cfg)")

    arrus.set_clog_level(arrus.logging.INFO)
    with create_gui(args.cfg, sequence=args.sequence, voltage=args.voltage, view=args.view,
                    host=args.host, port=args.port) as gui:
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
        # Blocks until the window is closed (qt) or Ctrl+C (web); returns at once for none.
        gui.run()


if __name__ == "__main__":
    main()
