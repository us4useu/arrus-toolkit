from gui4us.cfg.display import *

# Display configuration file.
displays = {
    "OXZ": Display2D(
        title=f"OXZ B-mode",
        layers=(
            Layer2D(
                value_range=(0, 60),
                cmap="gray",
                input=StreamDataId("default", 0),
            ),
        ),
        ax_labels=("OX (m)", "OZ (m)")
    ),
    "OYZ": Display2D(
         title=f"OYZ B-mode",
         layers=(
             Layer2D(
                 value_range=(0, 60),
                 cmap="gray",
                 input=StreamDataId("default", 1),
             ),
         ),
         ax_labels=("OY (m)", "OZ (m)")
     ),
}

VIEW_CFG = ViewCfg(displays)
