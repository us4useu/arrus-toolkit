import numpy as np
import arrus.medium
from arrus.ops.us4r import TxRxSequence, TxRx, Tx, Rx, Aperture, Pulse
from typing import Tuple

from arrus_rca_utils.sequence import convert_to_system_sequence, RcaSequence
import probe_params


def create_sequence(
        medium: arrus.medium.Medium,
        center_frequency: float,
        n_periods: float,
        angles: np.ndarray,
        pri: float,
        sample_range: Tuple[int, int],
) -> Tuple[TxRxSequence, TxRxSequence]:
    """
    Returns TX/RX sequences for RCA probe.

    This function returns two sequences:
    - transmit with OX elements, receive with OY elements len(angles) times
    - transmit with OY elements, receive with OX elements len(angles) times

    First TX=OX, RX=OY len(angles) times, then TX=OY, RX=OX len(angles) times.

    NOTE: this function assumes the parameters as defined in the probe_params.
    You have to adjust the values there in order to get the proper sequence
    for your RCA probe.
    """
    # Transmit with OX elements, receive with OY elements
    sequence_xy = TxRxSequence(
        ops=[
            TxRx(
                tx=Tx(
                    # Transmit with all elements.
                    # Center == 0.0 means the center of the probe.
                    aperture=Aperture(center=0.0,
                                      size=probe_params.APERTURE_X.n_elements),
                    excitation=Pulse(
                        center_frequency=center_frequency,
                        n_periods=n_periods,
                        inverse=False
                    ),
                    # Plane wave (focus depth = inf).
                    focus=np.inf,
                    angle=angle,
                    speed_of_sound=medium.speed_of_sound
                ),
                rx=Rx(
                    # Receive with all elements.
                    aperture=Aperture(center=0.0,
                                      size=probe_params.APERTURE_Y.n_elements),
                    sample_range=sample_range,
                ),
                pri=pri
            )
            for angle in angles
        ],
        tgc_curve=[]  # Will be set later.
    )

    # Transmit with OY elements, receive with OX elements.
    sequence_yx = TxRxSequence(
        ops=[
            TxRx(
                tx=Tx(
                    # Transmit with all elements.
                    # Center == 0.0 means the center of the probe.
                    aperture=Aperture(
                        center=0.0,
                        size=probe_params.APERTURE_Y.n_elements),
                    excitation=Pulse(
                        center_frequency=center_frequency,
                        n_periods=n_periods,
                        inverse=False
                    ),
                    # Plane wave (focus depth = inf).
                    focus=np.inf,
                    angle=angle,
                    speed_of_sound=medium.speed_of_sound
                ),
                rx=Rx(
                    # Receive with all elements.
                    aperture=Aperture(
                        center=0.0,
                        size=probe_params.APERTURE_X.n_elements),
                    sample_range=sample_range,
                ),
                pri=pri
            )
            for angle in angles
        ],
        tgc_curve=[]  # Will be set later.
    )
    return sequence_xy, sequence_yx


def get_system_sequence(sequence_xy, sequence_yx, metadata):
    sequence = convert_to_system_sequence(
        sequences=[
            RcaSequence(
                sequence=sequence_xy,
                tx=probe_params.APERTURE_X, rx=probe_params.APERTURE_Y,
            ),
            RcaSequence(
                sequence=sequence_yx,
                tx=probe_params.APERTURE_Y, rx=probe_params.APERTURE_X,
            ),
        ],
        metadata=metadata
    )
    return sequence
