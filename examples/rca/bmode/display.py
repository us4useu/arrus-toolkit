from gui4us.cfg.display import *

# Display configuration file.
displays = {
    "XY": Display2D(
        title=f"XY sequence (one frame)",
        layers=(
            Layer2D(
                value_range=(20, 80),
                cmap="gray",
                input=StreamDataId("default", 0),
            ),
        ),
        ax_labels=("OX (m)", "OZ (m)")
    ),
    "YX": Display2D(
        title=f"YX sequence (one frame)",
        layers=(
            Layer2D(
                value_range=(20, 80),
                cmap="gray",
                input=StreamDataId("default", 1),
            ),
        ),
        ax_labels=("OY (m)", "OZ (m)")
    ),
}

VIEW_CFG = ViewCfg(displays)
