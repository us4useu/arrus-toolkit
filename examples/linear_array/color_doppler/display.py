from gui4us.model import *
from gui4us.cfg.display import *

BMODE_DRANGE = (60, 120)
COLOR_DRANGE = (-3, 3)
POWER_DRANGE = (60, 80)
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
                value_range=COLOR_DRANGE,
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
