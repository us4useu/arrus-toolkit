import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui4us.model import *  # noqa: E402,F403
from gui4us.cfg.display import *  # noqa: E402,F403

from needle_config import TX_ANGLES, TX_ANGLES_OUTPUT, BMODE_OUTPUT  # noqa: E402

# Top: B-mode (the currently selected plane wave).
# Bottom: the TX angle selected for each frame (a horizontal line when the angle is held) and the TX angle
# perpendicular to the detected needle (gaps: no needle detected), the most recent frame on the right.
displays = {
    "B-mode": Display2D(
        title="B-mode",
        layers=(
            Layer2D(
                value_range=(20, 80),
                cmap="gray",
                input=StreamDataId("default", BMODE_OUTPUT),
            ),
        ),
        ax_labels=("OX (m)", "OZ (m)"),
    ),
    "TX angle": Display1D(
        title="TX angle",
        input=StreamDataId("default", TX_ANGLES_OUTPUT),
        value_range=(float(np.min(TX_ANGLES)) - 2, float(np.max(TX_ANGLES)) + 2),
        ax_labels=("frame", "angle [deg]"),
        labels=("selected TX angle", "detected (perpendicular to needle)"),
    ),
}

VIEW_CFG = ViewCfg(
    displays,
    grid_spec=GridSpec(
        n_rows=4, n_columns=1,
        locations=(
            DisplayLocation(rows=(0, 3), columns=0, display_id="B-mode"),
            DisplayLocation(rows=3, columns=0, display_id="TX angle"),
        ),
    ),
)
