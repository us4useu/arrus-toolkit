from gui4us.model import *
from gui4us.cfg.display import *

# Display configuration file.

displays = {
    "OX": Display2D(
        title=f"B-mode",
        layers=(
            Layer2D(
                value_range=(-200, 200),
                cmap="viridis",
                input=StreamDataId("default", 0),
            ),
        ),
        ax_labels=("OX", "OZ")
    ),
}

VIEW_CFG = ViewCfg(displays)