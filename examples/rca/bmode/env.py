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
from arrus_rca_utils.sequence import convert_to_system_sequence, RcaSequence
from gui4us.model.envs.arrus import (
    UltrasoundEnv,
    ArrusEnvConfiguration,
    Curve
)

# DEMO parameters: Vermon 128+128 elements
ARRAY_X = probe_params.ProbeArray(
    n_elements=128,
    pitch=0.2e-3,
    start=0,
    arrangement="ox"
)
ARRAY_Y = probe_params.ProbeArray(
    n_elements=128,
    pitch=0.2e-3,
    start=128,
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
        DigitalDownConversion(decimation_factor=4, fir_order=64),
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
        DynamicRangeAdjustment(min=dr_min, max=dr_max),
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


def configure(session: arrus.Session):
    us4r = session.get_device("/Us4R:0")
    medium = arrus.medium.Medium(name="tissue", speed_of_sound=1540)
    angles = np.linspace(-10, 10, 64) * np.pi / 180  # [rad]
    center_frequency = 6e6  # [Hz]
    n_periods = 2
    sample_range = (0, 5 * 1024)
    pri = 400e-6
    # Imaging grid.
    y_grid = np.arange(-6e-3, 6e-3, 0.2e-3)
    x_grid = np.arange(-6e-3, 6e-3, 0.2e-3)
    z_grid = np.arange(0e-3, 43e-3, 0.2e-3)

    # Initial TGC curve.
    tgc_sampling_points = np.linspace(np.min(z_grid), np.max(z_grid), 10)
    tgc_values = np.linspace(14, 54, 10)

    # TX/RX PW sequence
    sequence_xy, sequence_yx = create_sequence(
        medium=medium,
        angles=angles,
        n_periods=n_periods,
        center_frequency=center_frequency,
        sample_range=sample_range,
        pri=pri
    )

    # Image reconstruction.
    fir_taps = scipy.signal.firwin(
        numtaps=64, cutoff=np.array([0.5, 1.5]) * center_frequency,
        pass_zero="bandpass", fs=us4r.current_sampling_frequency
    )
    pipeline = get_pwi_reconstruction(
        array_x=ARRAY_X,
        array_y=ARRAY_Y,
        y_grid=y_grid,
        x_grid=x_grid,
        z_grid=z_grid,
        fir_taps=fir_taps,
        sequence_xy=sequence_xy,
        sequence_yx=sequence_yx,
        dr_min=20, dr_max=80,
    )
    scheme = Scheme(
        tx_rx_sequence=get_system_sequence(
            sequence_xy=sequence_xy,
            sequence_yx=sequence_yx,
            probe_model=us4r.get_probe_model(),
            device_sampling_frequency=us4r.current_sampling_frequency
        ),
        processing=pipeline
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
