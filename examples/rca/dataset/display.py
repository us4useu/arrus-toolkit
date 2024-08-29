from gui4us.cfg.display import *

# Display configuration file.
displays = {
    "OXYZ": Display3D(
        title=f"OXYZ",
        layers=(
            Layer3D(
                input=StreamDataId("default", 3),
            ),
        ),
    ),
    "OXZ": Display2D(
        title=f"OXZ B-mode y = 0",
        layers=(
            Layer2D(
                value_range=(30, 75),
                cmap="gray",
                input=StreamDataId("default", 2),
            ),
        ),
        ax_labels=("OX", "OZ")
    ),
    "OYZ": Display2D(
        title=f"OYZ B-mode x = 0",
        layers=(
            Layer2D(
                value_range=(33, 75),
                cmap="gray",
                input=StreamDataId("default", 1),
            ),
        ),
        ax_labels=("OY", "OZ")
    ),
    "OXY": Display2D(
        title=f"OXY B-mode z = 20 mm",
        layers=(
            Layer2D(
                value_range=(33, 75),
                cmap="gray",
                input=StreamDataId("default", 1),
            ),
        ),
        ax_labels=("OY", "OZ")
    ),

}

VIEW_CFG = ViewCfg(displays)
