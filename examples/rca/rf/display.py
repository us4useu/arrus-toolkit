from gui4us.cfg.display import *

# Display configuration file.
displays = {
    "XY": Display2D(
        title=f"XY sequence (one frame)",
        layers=(
            Layer2D(
                value_range=(-100, 100),
                cmap="viridis",
                input=StreamDataId("default", 0),
            ),
        ),
        ax_labels=("channel #", "sample #")
    ),
    "YX": Display2D(
        title=f"YX sequence (one frame)",
        layers=(
            Layer2D(
                value_range=(-100, 100),
                cmap="viridis",
                input=StreamDataId("default", 1),
            ),
        ),
        ax_labels=("channel #", "sample #")
    ),
}

VIEW_CFG = ViewCfg(displays)
