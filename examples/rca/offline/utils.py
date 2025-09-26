import time
import numpy as np
import arrus.medium
import arrus_rca_utils.probe_params as probe_params
from arrus.devices.probe import ProbeModel
from arrus.ops.imaging import *
from arrus.ops.us4r import *
from arrus.ops.us4r import TxRx, Tx, Rx, Aperture, Pulse
from arrus.ops.us4r import (
    TxRxSequence
)
import pickle
from arrus.utils.imaging import *
from arrus_rca_utils.reconstruction import (
    ReconstructHriRca,
    GetFramesForRange,
    Concatenate,
    get_frame_ranges,
    get_rx_aperture_size,
    PipelineSequence,
    SelectBatch,
    Slice
)
from collections import deque
from arrus_rca_utils.sequence import convert_to_system_sequence, RcaSequence
from gui4us.model.envs.arrus import (
    UltrasoundEnv,
    ArrusEnvConfiguration,
    Curve
)


ARRAY_X = probe_params.ProbeArray(
    n_elements=64,
    pitch=0.28e-3,
    start=0,
    arrangement="ox"
)
ARRAY_Y = probe_params.ProbeArray(
    n_elements=64,
    pitch=0.28e-3,
    start=64,
    arrangement="oy"
)


# ------------------------------------------ SEQUENCE
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
                    aperture=Aperture(center=0.0, size=ARRAY_X.n_elements),
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
                    aperture=Aperture(center=0.0, size=ARRAY_Y.n_elements),
                    sample_range=sample_range,
                ),
                pri=pri
            )
            for angle in angles
        ],
    )
    sequence_yx = TxRxSequence(
        ops=[
            TxRx(
                tx=Tx(
                    # Transmit with all elements.
                    # Center == 0.0 means the center of the probe.
                    aperture=Aperture(center=0.0, size=ARRAY_Y.n_elements),
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
                    aperture=Aperture(center=0.0, size=ARRAY_X.n_elements),
                    sample_range=sample_range,
                ),
                pri=pri
            )
            for angle in angles
        ],
    )
    return sequence_xy, sequence_yx


def get_system_sequence(
        sequence_xy: TxRxSequence, sequence_yx: TxRxSequence,
        probe_model: ProbeModel,
        device_sampling_frequency: float
):
    return convert_to_system_sequence(
        sequences=[
            RcaSequence(
                sequence=sequence_xy,
                tx=ARRAY_X, rx=ARRAY_Y,
            ),
            RcaSequence(
                sequence=sequence_yx,
                tx=ARRAY_Y, rx=ARRAY_X,
            ),
        ],
        probe_model=probe_model,
        device_sampling_frequency=device_sampling_frequency
    )


# ------------------------------------------ RECONSTRUCTION
def reorder_rf(frames, aperture_size):
    return (
        GetFramesForRange(frames=frames, aperture_size=aperture_size),
        RemapToLogicalOrder()
    )

def to_hri(
        y_grid, x_grid, z_grid,
        array_tx, array_rx,
        sequence,
        fir_taps, name=None,
        aperture_arrangement="alternate"
):
    return (
        Transpose(axes=(0, 1, 3, 2)),
        FirFilter(taps=fir_taps),
        DigitalDownConversion(decimation_factor=4, fir_order=128),
        ReconstructHriRca(
            y_grid=y_grid, x_grid=x_grid, z_grid=z_grid,
            probe_tx=array_tx,
            probe_rx=array_rx,
            sequence=sequence,
            min_tang=-0.5, max_tang=0.5,
            name=name,
            arrangement=aperture_arrangement,
        ),
    )


def to_bmode(dr_min, dr_max):
    return (
        # Concatenate along TX axis
        Concatenate(axis=0),
        Mean(axis=0),
        Squeeze(),
        EnvelopeDetection(),
        LogCompression(),
        # DynamicRangeAdjustment(min=dr_min, max=dr_max),
        Squeeze(),
    )


def branch(steps, name=None):
    return Pipeline(steps=steps, name=name, placement="/GPU:0")


def get_pwi_reconstruction(
        y_grid, x_grid, z_grid,
        array_x: probe_params.ProbeArray, array_y: probe_params.ProbeArray,
        sequence_xy: TxRxSequence,
        sequence_yx: TxRxSequence,
        fir_taps,
        dr_min=20, dr_max=80,
):
    seqs = sequence_xy, sequence_yx
    range_xy, range_yx = get_frame_ranges(*seqs)
    ap_size_xy, ap_size_yx = get_rx_aperture_size(*seqs)


    reconstruction = Pipeline(
        steps=(
            branch(
                name="b",
                steps=(
                    # TX=OY, RX=OX
                    *reorder_rf(frames=range_yx, aperture_size=ap_size_yx),
                    *to_hri(
                        fir_taps=fir_taps,
                        y_grid=y_grid,
                        x_grid=x_grid,
                        z_grid=z_grid,
                        array_tx=array_y,
                        array_rx=array_x,
                        sequence=sequence_yx,
                    ),
                )),
            # TX=OX, RX=OY
            *reorder_rf(frames=range_xy, aperture_size=ap_size_xy),
            *to_hri(
                fir_taps=fir_taps,
                y_grid=y_grid,
                x_grid=x_grid,
                z_grid=z_grid,
                array_tx=array_x, array_rx=array_y,
                sequence=sequence_xy,
            )
        ),
        placement="/GPU:0"
    )

    last_time = time.time()
    def log_frame_rate(data):
        nonlocal last_time
        print(f"Current frame rate: {1.0/(time.time() - last_time)}")
        last_time = time.time()
        return data  
    
    # Expected input:
    bmodes = Pipeline(
        name="bmode",
        steps=(
            SelectBatch([0, 1]),
            *to_bmode(
                dr_min=dr_min,
                dr_max=dr_max
            ),
            # (y, x, z)
	    Output(),
            branch(
                steps=(
                    Slice(axis=0),
                    Squeeze(),
                    Transpose(),
            )),
            branch(
                steps=(
                    Slice(axis=1),
                    Squeeze(),
                    Transpose(),
            ))
        ),
        placement="/GPU:0"
    )
    pipelines = [reconstruction, bmodes]
    return PipelineSequence(pipelines)
