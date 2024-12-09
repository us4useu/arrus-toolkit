import time

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
import arrus.logging

arrus.logging.set_clog_level(arrus.logging.TRACE)

def configure(session: arrus.Session):
    medium = arrus.medium.Medium(name="ats549", speed_of_sound=1450)
    us4r = session.get_device("/Us4R:0")

    n_elements = us4r.get_probe_model().n_elements
    sampling_frequency = us4r.sampling_frequency

    # Parameters
    n_samples = 2048
    n_frames = 65
    tx_aperture = [True] * n_elements

    # Initial TGC curve.
    tgc_sampling_points = np.linspace(0, n_samples/sampling_frequency, 10)
    tgc_values = np.linspace(14, 54, 10)

    sequence = TxRxSequence(
        ops=[
            TxRx(
                Tx(aperture=tx_aperture,
                   excitation=Pulse(
                       center_frequency=6e6, n_periods=2,
                       inverse=False
                   ),
                   delays=[0] * n_elements),
                Rx(aperture=[True] * n_elements,
                   sample_range=(0, n_samples),
                   downsampling_factor=1),
                pri=2000e-6
            ),
        ]*n_frames,
        tgc_curve=[],  # [dB]
    )
    start = time.time()

    def print_frame_rate(data):
        nonlocal start
        print(f"Current frame rate: {1/(time.time()-start)}", end="\r")
        start = time.time()
        return data

    pipeline = Pipeline(
        steps=(
            RemapToLogicalOrder(),
            SelectFrames([0]),
            Squeeze(),
            Lambda(print_frame_rate)
        ),
        placement="/GPU:0"
    )
    return ArrusEnvConfiguration(
        medium=medium,
        scheme=arrus.ops.us4r.Scheme(
            tx_rx_sequence=sequence,
            processing=pipeline,
            work_mode="ASYNC"
        ),
        tgc=Curve(points=tgc_sampling_points, values=tgc_values),
        voltage=10
    )


ENV = UltrasoundEnv(
    session_cfg="external_trigger.prototxt",
    configure=configure,
)
