import math
import numpy as np
from arrus.ops.us4r import *
from arrus.ops.imaging import *
from arrus.utils.imaging import *
import cupy as cp
from collections import deque

virtual_source_z = [-13e-3]  # [m]
prf = 5e3  # [Hz]
# Assumed speed of sound.
speed_of_sound = 1450  # [m/s]
n_samples = 4096  # TODO replace with max depth
tx_frequency = 3e6  # [Hz]
n_periods = 2
#  Imaged area.
x_grid = np.arange(-5, 5, 0.15) * 1e-3  # [m]
y_grid = np.arange(-5, 5, 0.15) * 1e-3  # [m]
z_grid = np.arange(0, 60, 0.15) * 1e-3  # [m]
max_depth = np.max(z_grid)
tx_voltage = 5  # [V]
tgc_t = np.linspace(0, max_depth, 10)/speed_of_sound  # [s]
tgc_values = np.linspace(14, 54, 10)  # [dB]

def get_dwi_delays(probe_model, virtual_source_z, speed_of_sound):
    """
    This function assumes a probe with 3 intermediate rows (as it is prepared
    by Vermon).
    """
    pitch = probe_model.pitch
    n_elements = probe_model.n_elements
    n_ox = int(round(math.sqrt(n_elements)))
    assert n_ox ** 2 == n_elements, "Only square probes are supported"
    n_rows = n_ox + 3
    x = (np.arange(0, n_ox) - n_ox // 2 + 0.5) * pitch
    y = (np.arange(0, n_rows) - n_rows // 2 + 0.5) * pitch
    x = np.atleast_2d(x)
    y = np.atleast_2d(y).T
    delays = (np.sqrt(
        virtual_source_z ** 2 + x ** 2 + y ** 2) - virtual_source_z) / speed_of_sound
    # Remove the "intermediate" rows.
    delays = np.delete(delays, (n_ox // 4, n_ox // 2 + 1, 3 * n_ox // 4 + 2),
                       axis=0)
    return delays.flatten()


def get_dwi_sequence(probe_model):
    n_elements = probe_model.n_elements
    ops = [TxRx(
        tx=Tx(
            aperture=[True] * n_elements,  # Full TX aperture.
            excitation=Pulse(center_frequency=tx_frequency, n_periods=n_periods,
                             inverse=False),
            delays=get_dwi_delays(probe_model, vs, speed_of_sound)
        ),
        rx=Rx(
            # Full RX aperture
            # (note: will be automatically splitted to multiple RX sub-aperts.)
            aperture=[True] * n_elements,
            sample_range=(0, n_samples)
        ),
        pri=1 / prf
    ) for vs in virtual_source_z]
    return TxRxSequence(
        ops=ops,
        tgc_curve=np.ndarray([]),  # Will be set later.
        # How many times this sequence should be repeated before
        # starting data transfer from us4R to host PC.
        n_repeats=1,
    )


def get_pwi_sequence(probe_model):
    # TODO make it to accept any zx and zy angles
    n_elements = probe_model.n_elements
    return TxRxSequence(
        ops=[
            TxRx(
                Tx(aperture=[True] * n_elements,
                   excitation=Pulse(
                       center_frequency=tx_frequency, n_periods=n_periods,
                       inverse=False),
                   # Custom delays 1.
                   delays=[0]*n_elements),
                Rx(aperture=[True]*n_elements,
                   sample_range=(0, n_samples),
                   downsampling_factor=1),
                pri=1/prf
            ),
        ],
        tgc_curve=[]
    )


def get_imaging(sequence, tx_foc, tx_ang_zx, tx_ang_zy,
                n_last_frames_to_save=10):
    z_grid_size = len(z_grid)
    x_grid_size = len(x_grid)
    y_grid_size = len(y_grid)
    rf_queue = deque(maxlen=n_last_frames_to_save)
    print(x_grid_size)
    print(y_grid_size)
    pipeline = Pipeline(
        steps=(
            RemapToLogicalOrder(),
            Transpose(axes=(0, 1, 3, 2)),
            Reshape(1, len(sequence.ops), 32, 32, n_samples),
            Lambda(lambda data: (rf_queue.append(data.get()), data)[1]),
            BandpassFilter(),
            QuadratureDemodulation(),
            Decimation(decimation_factor=20, cic_order=2),
            ReconstructLri3D(
                x_grid=x_grid, y_grid=y_grid, z_grid=z_grid,
                tx_foc=tx_foc,
                tx_ang_zx=tx_ang_zx, tx_ang_zy=tx_ang_zy,
                speed_of_sound=speed_of_sound),
            Squeeze(),
            EnvelopeDetection(),
            LogCompression(),
            # Get the center
            Lambda(lambda data: data, prepare_func=lambda metadata: (print(metadata.input_shape), metadata)[1]),
            Lambda(
                lambda data: cp.concatenate((
                    data[y_grid_size // 2, :, :].T,
                    data[:, x_grid_size // 2, :].T), axis=1),
                lambda metadata: metadata.copy(
                    input_shape=(z_grid_size, x_grid_size + y_grid_size))),
        ),
        placement="/GPU:0"
    )
    return pipeline, rf_queue


def get_dwi_imaging(**kwargs):
    return get_imaging(
        tx_ang_zx=np.array([0.0]), tx_ang_zy=np.array([0.0]),
        tx_foc=np.array([virtual_source_z]),
        **kwargs
    )


def get_pwi_imaging(**kwargs):
    return get_imaging(
        tx_ang_zx=np.array([0.0]), tx_ang_zy=np.array([0.0]),
        tx_foc=np.array([np.inf]),
        **kwargs
    )
