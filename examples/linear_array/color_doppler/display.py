from gui4us.model import *
from gui4us.cfg.display import *

# Display configuration file.
displays = {
    "ColorDoppler": Display2D(
        title=f"Color Doppler",
        layers=(
            Layer2D(
                value_range=(60, 120),
                cmap="gray",
                input=StreamDataId("default", 0),
            ),
            Layer2D(
                value_range=(-3000e-3, 3000e-3),
                cmap="bwr",
                input=StreamDataId("default", 1),
            ),
        ),
    ),
    "PowerDoppler": Display2D(
        title=f"Power Doppler",
        layers=(
            Layer2D(
                value_range=(60, 120),
                cmap="gray",
                input=StreamDataId("default", 0),
            ),
            Layer2D(
                value_range=(0, 200),
                cmap="hot",
                input=StreamDataId("default", 2),
            ),
        ),
    ),
}

VIEW_CFG = ViewCfg(displays)
