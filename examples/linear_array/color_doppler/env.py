import arrus.medium
import numpy as np
from arrus.utils.imaging import *
from arrus.ops.imaging import *
from arrus.ops.us4r import *
from gui4us.model.envs.arrus import (
    UltrasoundEnv,
    ArrusEnvConfiguration,
    Curve,
    get_depth_range
)
from ops import ReconstructDoppler, FilterWallClutter, CreateDopplerFrame
import cupy as cp
from display import BMODE_DRANGE, COLOR_DRANGE, POWER_DRANGE
import arrus.logging


def create_sequence(
        angles: np.ndarray,
        n_periods: float,
        center_frequency: float,
        speed_of_sound: float,
        sample_range: Tuple[int, int],
        pri: float,
) -> TxRxSequence:
    ops = []
    rx = Rx(
        aperture=Aperture(center=0.0),
        sample_range=sample_range,
        downsampling_factor=1,
    )
    for angle in angles:
        tx = Tx(
            aperture=Aperture(center=0.0),
            excitation=Pulse(
                center_frequency=center_frequency,
                n_periods=n_periods,
                inverse=False
            ),
            focus=np.inf,
            angle=angle,
            speed_of_sound=speed_of_sound,
        )
        txrx = TxRx(tx, rx, pri)
        ops.append(txrx)
    return TxRxSequence(ops)


def concatenate_sequences(*seqs) -> TxRxSequence:
    ops = []
    for s in seqs:
        ops.extend(s.ops)
    return TxRxSequence(ops=ops)


def configure(session: arrus.Session):
    medium = arrus.medium.Medium(name="human_carotid", speed_of_sound=1540)
    probe_model = session.get_device("/Us4R:0").get_probe_model()
    # Imaging grid.
    x_grid = np.arange(probe_model.x_min, probe_model.x_max, 0.1e-3)
    z_grid = np.arange(0e-3, 25e-3, 0.1e-3)

    # Initial TGC curve.
    tgc_sampling_points = np.linspace(np.min(z_grid), np.max(z_grid), 10)
    tgc_values = np.linspace(34, 54, 10)

    # Params
    fs = session.get_device("/Us4R:0").current_sampling_frequency
    center_frequency = 6e6
    doppler_angle = 10  # [deg]
    n_tx_doppler = 64
    n_tx_bmode = 7
    pri = 100e-6
    sample_range = (0, 4*512)

    # Filter taps
    fir_taps = scipy.signal.firwin(
        numtaps=64,
        cutoff=np.array([0.5, 1.5]) * center_frequency,
        pass_zero="bandpass",
        fs=fs,
    )

    # Sequence
    doppler_sequence = create_sequence(
        angles=np.tile(doppler_angle*np.pi/180, n_tx_doppler),
        n_periods=16,
        center_frequency=center_frequency,
        speed_of_sound=medium.speed_of_sound,
        sample_range=sample_range,
        pri=pri,
    )
    bmode_sequence = create_sequence(
        angles=np.linspace(-10, 10, n_tx_bmode)*np.pi/180,
        n_periods=2,
        center_frequency=center_frequency,
        speed_of_sound=medium.speed_of_sound,
        sample_range=sample_range,
        pri=pri,
    )
    # NOTE: the order of sequences matters in the pipeline definition
    # (check SelectFrames values)
    sequence = concatenate_sequences(doppler_sequence, bmode_sequence)

    # Pipeline.
    pipeline = Pipeline(
        steps=(
            RemapToLogicalOrder(),
            Transpose(axes=(0, 1, 3, 2)),
            FirFilter(taps=fir_taps),
            QuadratureDemodulation(),
            Decimation(decimation_factor=4, cic_order=2),
            Pipeline(
                # -> Color Doppler.
                steps=(
                    SelectFrames(frames=np.arange(0, n_tx_doppler)),
                    ReconstructLri(x_grid=x_grid, z_grid=z_grid),
                    Output(),
                    Squeeze(),
                    FilterWallClutter(wn=0.5, n=8, ftype='butter', btype='highpass'),
                    ReconstructDoppler(),
                    Pipeline(
                        steps=(
                            CreateDopplerFrame(
                                color_dynamic_range=COLOR_DRANGE,
                                power_dynamic_range=POWER_DRANGE,
                                frame_type="power"
                            ),
                            Transpose(),
                        ),
                        placement="/GPU:0"
                    ),
                    CreateDopplerFrame(
                        color_dynamic_range=COLOR_DRANGE,
                        power_dynamic_range=POWER_DRANGE,
                        frame_type="color",
                    ),
                    Transpose(),
                ),
                placement="/GPU:0"
            ),
            # -> B-modes
            SelectFrames(frames=np.arange(n_tx_doppler, n_tx_doppler+n_tx_bmode)),
            ReconstructLri(x_grid=x_grid, z_grid=z_grid),
            Mean(axis=1),  # Average LRIs
            EnvelopeDetection(),
            Mean(axis=0),  # Average batch
            Squeeze(),
            Transpose(),
            LogCompression(),
        ),
        placement="/GPU:0"
    )
    return ArrusEnvConfiguration(
        medium=medium,
        scheme=arrus.ops.us4r.Scheme(
            tx_rx_sequence=sequence,
            processing=pipeline
        ),
        tgc=Curve(points=tgc_sampling_points, values=tgc_values),
        voltage=10
    )


ENV = UltrasoundEnv(
    session_cfg="us4r.prototxt",
    configure=configure,
)
