from arrus_rca_utils.probe_params import *


# DEMO parameters: Vermon 128+128 elements
APERTURE_X = ProbeArray(
    n_elements=128,
    pitch=0.2e-3,
    start=0,
    arrangement="ox"
)
APERTURE_Y = ProbeArray(
    n_elements=128,
    pitch=0.2e-3,
    start=128,
    arrangement="oy"
)
