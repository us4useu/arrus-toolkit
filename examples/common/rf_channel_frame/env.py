import arrus.medium
from arrus.utils.imaging import *
from arrus.ops.imaging import *
import arrus.utils.imaging
from arrus.ops.us4r import *
from gui4us.model.envs.arrus import (
    UltrasoundEnv,
    ArrusEnvConfiguration,
    Curve,
    get_depth_range
)
import numpy as np


def configure(session: arrus.Session):
    medium = arrus.medium.Medium(name="ats549", speed_of_sound=1450)
    sampling_frequency = session.get_device("/Us4R:0").sampling_frequency
    # Imaging grid.
    n_samples = 2048

    # Initial TGC curve.
    tgc_sampling_points = np.linspace(0, n_samples/sampling_frequency, 10)
    tgc_values = np.linspace(14, 54, 10)

    sequence = PwiSequence(
        angles=np.array([0]),
        pulse=Pulse(center_frequency=18e6, n_periods=2, inverse=False),
        rx_sample_range=(0, n_samples),
        speed_of_sound=medium.speed_of_sound,
        pri=200e-6,
    )
    pipeline = Pipeline(
        steps=(
            RemapToLogicalOrder(),
            Squeeze(),
        ),
        placement="/GPU:0"
    )
    return ArrusEnvConfiguration(
        scheme=arrus.ops.us4r.Scheme(
            tx_rx_sequence=sequence,
            processing=pipeline
        ),
        tgc=Curve(points=tgc_sampling_points, values=tgc_values),
        voltage=5
    )


ENV = UltrasoundEnv(
    session_cfg="C:/Users/user/Documents/Github/arrus-toolkit/us4r.prototxt",
    configure=configure,
)
