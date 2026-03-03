from typing import Tuple

import numpy as np
import arrus.medium
from arrus.devices.probe import ProbeModel
from arrus.ops.imaging import *
from arrus.ops.us4r import *
from arrus.ops.us4r import TxRx, Tx, Rx, Aperture, Pulse
from arrus.ops.us4r import (
    TxRxSequence
)
from arrus.utils.imaging import *
from gui4us.model.envs.arrus import (
    UltrasoundEnv,
    ArrusEnvConfiguration,
    Curve
)

# ------------------------------------------ SEQUENCE
def create_pw_sequence(
        medium: arrus.medium.Medium,
        center_frequency: float,
        n_periods: float,
        angles: np.ndarray,
        pri: float,
        sample_range: Tuple[int, int],
        array_tx, array_rx,
        array_tx_id: str, array_rx_id: str,
        name: str
) -> Tuple[TxRxSequence, TxRxSequence]:
    """
    Returns PW sequence for RCA probe.
    """
    # Transmit with OX elements, receive with OY elements
    return TxRxSequence(
        ops=[
            TxRx(
                tx=Tx(
                    # Transmit with all elements.
                    # Center == 0.0 means the center of the probe.
                    aperture=Aperture(center=0.0, size=array_tx.n_elements),
                    excitation=Pulse(
                        center_frequency=center_frequency,
                        n_periods=n_periods,
                        inverse=False
                    ),
                    # Plane wave (focus depth = inf).
                    focus=np.inf,
                    angle=angle,
                    speed_of_sound=medium.speed_of_sound,
                    placement=array_tx_id
                ),
                rx=Rx(
                    # Receive with all elements.
                    aperture=Aperture(center=0.0, size=array_tx.n_elements),
                    sample_range=sample_range,
                    placement=array_tx_id
                ),
                pri=pri
            )
            for angle in angles
        ],
        name=name
    )


def create_processing(name: str, angles):
    return Pipeline(
        steps=(
            RemapToLogicalOrder(),
            Squeeze(),
            SelectFrames([len(angles)//2]),
            Squeeze(),
        ),
        placement="/GPU:0",
        name=name
    )



def configure(session: arrus.Session):
    us4r = session.get_device("/Us4R:0")

    # The ordinal number of the OX and OY sub-arrays. See us4r.prototxt
    oy_ordinal = 0
    ox_ordinal = 1
    array_ox = us4r.get_probe_model(ordinal=ox_ordinal)
    array_oy = us4r.get_probe_model(ordinal=oy_ordinal)
    array_ox_id = f"Probe:{ox_ordinal}"
    array_oy_id = f"Probe:{oy_ordinal}"

    sampling_frequency = us4r.sampling_frequency
    n_samples = 4*1024

    medium = arrus.medium.Medium(name="ats549", speed_of_sound=1450)
    angles = np.linspace(-10, 10, 32) * np.pi / 180  # [rad]
    center_frequency = 6e6  # [Hz]
    n_periods = 2
    sample_range = (0, n_samples)
    pri = 400e-6

    common_parameters = dict(
        medium=medium,
        angles=angles,
        n_periods=n_periods,
        center_frequency=center_frequency,
        sample_range=sample_range,
        pri=pri,
    )

    # Initial TGC curve.
    tgc_sampling_points = np.linspace(0, n_samples / sampling_frequency, 10)
    tgc_values = np.linspace(14, 44, 10)

    # TX with OX elements, RX with OY elements
    sequence_xy = create_pw_sequence(
        array_tx_id=array_ox_id,
        array_tx=array_ox,
        array_rx_id=array_oy_id,
        array_rx=array_oy,
        name="XY",
        **common_parameters
    )
    sequence_yx = create_pw_sequence(
        array_tx_id=array_oy_id,
        array_tx=array_oy,
        array_rx_id=array_ox_id,
        array_rx=array_ox,
        name="YX",
        **common_parameters
    )

    preprocess_rf_xy = create_processing("preprocess_XY", angles)
    preprocess_rf_yx = create_processing("preprocess_YX", angles)

    processing = Graph(
        operations={preprocess_rf_xy, preprocess_rf_yx},
        dependencies={
            "Output:0": preprocess_rf_xy.name,
            "Output:1": preprocess_rf_yx.name,
            preprocess_rf_xy.name: sequence_xy.name,
            preprocess_rf_yx.name: sequence_yx.name
        }
    )
    scheme = Scheme(
        tx_rx_sequence=[
            sequence_xy,
            sequence_yx
        ],
        processing=processing
    )
    return ArrusEnvConfiguration(
        medium=medium,
        scheme=scheme,
        tgc=Curve(points=tgc_sampling_points, values=tgc_values),
        voltage=5
    )


ENV = UltrasoundEnv(
    session_cfg=str(Path(__file__).parent / "us4r.prototxt"),
    configure=configure,
)
