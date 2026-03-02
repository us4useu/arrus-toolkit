from typing import Tuple

import arrus.medium
from arrus.ops.us4r import *
import numpy as np

def get_pw_sequence(
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

    :param array_tx: the probe to be used on TX (e.g. us4r.get_probe_model(i))
    :param array_rx: the probe to be used on RX (e.g. us4r.get_probe_model(i))
    :param array_tx_id: the id of the probe to be used on TX (ordinal number, e.g. `i`)
    :param array_rx_id: the id of the probe to be used on RX (ordinal number e.g. `i`)
    """
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
                    aperture=Aperture(center=0.0, size=array_rx.n_elements),
                    sample_range=sample_range,
                    placement=array_rx_id
                ),
                pri=pri
            )
            for angle in angles
        ],
        name=name
    )

