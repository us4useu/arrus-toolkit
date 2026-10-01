"""The pw_needle_tracking example settings (shared by env.py and display.py; no side effects on import)."""
import os

import numpy as np

from needle_tracking import DetectorParams


def _env(name, default, type_=float):
    return type_(os.environ.get(name, default))


SESSION_CFG = os.path.expanduser(os.environ.get("ARRUS_SESSION_CFG", "~/us4r.prototxt"))
VOLTAGE = _env("ARRUS_VOLTAGE", 5, int)  # [V]
SPEED_OF_SOUND = _env("NEEDLE_SPEED_OF_SOUND", 1450)  # [m/s]
CENTER_FREQUENCY = _env("NEEDLE_CENTER_FREQUENCY", 6e6)  # [Hz]
SEQUENCE_NAME = "needle"
TX_ANGLE_PARAMETER = f"/Us4R:0/{SEQUENCE_NAME}/txAngle"

# The TX angles available at runtime [deg] (one TX delay profile each); also the exploration sweep.
TX_ANGLES = np.arange(_env("NEEDLE_TX_ANGLE_MIN", -10), _env("NEEDLE_TX_ANGLE_MAX", 10) + 1e-6,
                      _env("NEEDLE_TX_ANGLE_STEP", 1))
BUFFER_SIZE = _env("NEEDLE_BUFFER_SIZE", 5, int)
HISTORY_SIZE = 200  # frames, the bottom display
SHOW_ARRUS_TIMING = _env("NEEDLE_SHOW_ARRUS_TIMING", 1, int) != 0
# Lines closer than NEEDLE_MIN_LINE_ANGLE to horizontal are ignored: horizontal specular layers (e.g. the phantom
# bottom, layered samples) would otherwise always win over the needle; 0: accept all the angles.
DETECTOR_PARAMS = DetectorParams(min_abs_line_angle=_env("NEEDLE_MIN_LINE_ANGLE", 3))

# The pipeline outputs (see the pipeline in NeedleTrackingEnv._configure).
TX_ANGLES_OUTPUT = 0
BMODE_OUTPUT = 1
