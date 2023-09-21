
# Display configuration file.

displays = {
    "BMODE": Display2D(
        title=f"B-mode",
        layers=(
            Layer2D(
                value_range=(20, 80),
                cmap="gray",
                input=StreamDataId("default", 0),
            ),
        ),
        ax_labels=("OX (m)", "OZ (m)")
    ),
    "NN-B-mode": Display2D(
        title=f"NN-B-mode",
        layers=(
            Layer2D(
                value_range=(100, 200),
                cmap="gray",
                input=StreamDataId("default", 1),
            ),
        ),
        ax_labels=("OX (m)", "OZ (m)")
    ),
}

VIEW_CFG = ViewCfg(displays)
