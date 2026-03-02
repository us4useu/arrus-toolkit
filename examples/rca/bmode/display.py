from gui4us.cfg.display import *

# Display configuration file.
displays = {
    "XY": Display2D(
        title=f"y = 0",
        layers=(
            Layer2D(
                value_range=(45, 80),
                cmap="gray",
                input=StreamDataId("default", 0),
            ),
        ),
        ax_labels=("OX (m)", "OZ (m)")
    ),
    "YX": Display2D(
        title=f"x = 0",
        layers=(
            Layer2D(
                value_range=(45, 80),
                cmap="gray",
                input=StreamDataId("default", 1),
            ),
        ),
        ax_labels=("OY (m)", "OZ (m)")
    ),
}

VIEW_CFG = ViewCfg(displays)
