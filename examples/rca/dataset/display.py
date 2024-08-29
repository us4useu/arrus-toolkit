from gui4us.cfg.display import *

# Display configuration file.
displays = {
    "OXZ": Display2D(
        title=f"OXZ B-mode y = 0",
        layers=(
            Layer2D(
                value_range=(35, 80),
                cmap="gray",
                input=StreamDataId("default", 0),
            ),
        ),
        ax_labels=("OX (m)", "OZ (m)")
    ),
    "OYZ": Display2D(
        title=f"OYZ B-mode x = 0",
        layers=(
            Layer2D(
                value_range=(35, 80),
                cmap="gray",
                input=StreamDataId("default", 1),
            ),
        ),
        ax_labels=("OY (m)", "OZ (m)")
    ),
    "OXYZ": Display3D(
        title=f"OXYZ",
        layers=(
            Layer3D(
                input=StreamDataId("default", 2),
            ),
        ),
    ),
}

VIEW_CFG = ViewCfg(displays)
