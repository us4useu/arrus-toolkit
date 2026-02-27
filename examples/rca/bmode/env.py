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
from reconstruction import Slice, ReconstructHriRca

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


def create_processing(name: str):
    return Pipeline(
        steps=(
            RemapToLogicalOrder(),
        ),
        placement="/GPU:0",
        name=name
    )

def to_hri(
        y_grid, x_grid, z_grid,
        fir_taps,
        tx_orientation: str,
        rx_orientation: str,
        name: str,
):
    return Pipeline(
        steps=(
            Transpose(axes=(0, 1, 3, 2)),
            FirFilter(taps=fir_taps),
            DigitalDownConversion(decimation_factor=4, fir_order=64),
            ReconstructHriRca(
                 y_grid=y_grid, x_grid=x_grid, z_grid=z_grid,
                 min_tang=-0.5, max_tang=0.5,
                 name=name,
                 tx_orientation=tx_orientation,
                 rx_orientation=rx_orientation
             ),
        ),
        placement="/GPU:0",
        name=name
    )


def to_bmode(dr_min, dr_max, name: str):
    return Pipeline(
        steps=(
             Mean(axis=0),  # Just to reduce the batch axis
            Squeeze(),
             EnvelopeDetection(),
             LogCompression(),
             DynamicRangeAdjustment(min=dr_min, max=dr_max),
             Squeeze(),
        ),
        placement="/GPU:0",
        name=name
    )


def to_slice(axis, name: str):
    return Pipeline(
        steps=(
            Slice(axis=axis),
            Transpose()
        ),
        placement="/GPU:0",
        name=name
    )


def configure(session: arrus.Session):
    us4r = session.get_device("/Us4R:0")

    # The ordinal number of the OX and OY sub-arrays. See us4r.prototxt
    ox_ordinal = 0
    oy_ordinal = 1
    array_ox = us4r.get_probe_model(ordinal=ox_ordinal)
    array_oy = us4r.get_probe_model(ordinal=oy_ordinal)
    array_ox_id = f"Probe:{ox_ordinal}"
    array_oy_id = f"Probe:{oy_ordinal}"

    sampling_frequency = us4r.sampling_frequency
    n_samples = 4*1024

    medium = arrus.medium.Medium(name="ats549", speed_of_sound=1480)
    angles = np.linspace(-10, 10, 32) * np.pi / 180  # [rad]
    center_frequency = 6e6  # [Hz]
    n_periods = 2
    sample_range = (0, n_samples)
    pri = 400e-6

    # Imaging grid.
    y_grid = np.arange(-8e-3, 8e-3, 0.2e-3)
    x_grid = np.arange(-8e-3, 8e-3, 0.2e-3)
    z_grid = np.arange(5e-3, 40e-3, 0.2e-3)

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
    tgc_values = np.linspace(24, 54, 10)

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

    # Image reconstruction.
    fir_taps = scipy.signal.firwin(
        numtaps=64, cutoff=np.array([0.5, 1.5]) * center_frequency,
        pass_zero="bandpass", fs=us4r.current_sampling_frequency
    )

    to_hri_xy = to_hri(
        name="to_hri_xy",
        x_grid=x_grid, y_grid=y_grid, z_grid=z_grid,
        fir_taps=fir_taps,
        tx_orientation="ox",
        rx_orientation="oy"
    )

    # TODO merge HRI xy and HRI yx
    to_bmode_all = to_bmode(dr_min=20, dr_max=80, name="to_bmode")
    to_slice_xz = to_slice(axis=0, name="to_slice_xz")
    to_slice_yz = to_slice(axis=1, name="to_slice_yz")

    preprocess_xy = create_processing(name="preprocess_xy")
    preprocess_yx = create_processing(name="preprocess_yx")

    processing = Graph(
        operations={
            to_hri_xy, to_bmode_all, to_slice_xz, to_slice_yz,
            preprocess_xy,
            preprocess_yx
        },
        dependencies={
            "Output:0": to_slice_xz.name,
            "Output:1": to_slice_yz.name,
            "Output:2": preprocess_xy.name,
            "Output:3": preprocess_yx.name,
            to_slice_yz.name: to_bmode_all.name,
            to_slice_xz.name: to_bmode_all.name,
            to_bmode_all.name: to_hri_xy.name,
            to_hri_xy.name: preprocess_xy.name,
            preprocess_xy.name: sequence_xy.name,
            preprocess_yx.name: sequence_yx.name
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
        voltage=20
    )


ENV = UltrasoundEnv(
    session_cfg=str(Path(__file__).parent / "us4r.prototxt"),
    configure=configure,
)
