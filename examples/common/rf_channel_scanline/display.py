from gui4us.model import *
from gui4us.cfg.display import *

# Display configuration file.

displays = {
    "scanline": Display1D(
        title=f"Scanlines",
        ax_labels=("# Sample", "Amplitude (a.u.)"),
        value_range=(-2**12, 2**12)
    ),
}

VIEW_CFG = ViewCfg(displays)
