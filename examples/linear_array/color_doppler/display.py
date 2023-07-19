from gui4us.model import *
from gui4us.cfg.display import *

# Display configuration file.
displays = {
    "OX": Display2D(
        title=f"B-mode",
        layers=(
            Layer2D(
                value_range=(20, 80),
                cmap="gray",
                input=StreamDataId("default", 0),
            ),
            Layer2D(
                value_range=(-300e-3, 300e-3),
                cmap="bwr",
                input=StreamDataId("default", 1),
            ),
        ),
    ),
}

VIEW_CFG = ViewCfg(displays)
