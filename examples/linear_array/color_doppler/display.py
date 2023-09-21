from gui4us.model import *
from gui4us.cfg.display import *
import numpy as np

BMODE_DRANGE = (60, 120)
COLOR_DRANGE = np.array((0, np.pi/2))
POWER_DRANGE = (50, 80)

crange = np.array(
    [-np.abs(COLOR_DRANGE).max(), np.abs(COLOR_DRANGE).max()]
)/2

# Display configuration file.
displays = {
    "ColorDoppler": Display2D(
        title=f"Color Doppler",
        layers=(
            Layer2D(
                value_range=BMODE_DRANGE,
                cmap="gray",
                input=StreamDataId("default", 0),
            ),
            Layer2D(
                value_range=crange,
                cmap="bwr",
                input=StreamDataId("default", 1),
            ),
        ),
    ),
    "PowerDoppler": Display2D(
        title=f"Power Doppler",
        layers=(
            Layer2D(
                value_range=BMODE_DRANGE,
                cmap="gray",
                input=StreamDataId("default", 0),
            ),
            Layer2D(
                value_range=POWER_DRANGE,
                cmap="hot",
                input=StreamDataId("default", 2),
            ),
        ),
    ),
}

VIEW_CFG = ViewCfg(displays)
