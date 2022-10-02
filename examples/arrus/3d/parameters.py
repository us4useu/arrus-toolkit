import math
import numpy as np
from arrus.ops.us4r import *
from arrus.ops.imaging import *
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
tx_voltage = 5  # [V]


def get_delays(tx_focus, tx_ang_zx, tx_ang_zy, probe_model, speed_of_sound):
    pitch = probe_model.pitch
    n_elements = probe_model.n_elements
    return get_delays_raw(tx_focus, tx_ang_zx, tx_ang_zy, pitch,
                          n_elements, speed_of_sound)


def get_delays_raw(tx_focus, tx_ang_zx, tx_ang_zy, pitch, n_elements,
                   speed_of_sound):
    n_x = int(round(math.sqrt(n_elements)))
    assert n_x ** 2 == n_elements, "Only square probes are supported"
    # Assuming, that here are 4 groups of 256 elements separated by a single
    # dead row.
    n_y = n_x + 3
    # Positions of the elements. Assuming that the coordinate system origin
    # (0, 0, 0) is in the center of probe.
    x = (np.arange(0, n_x) - n_x//2 + 0.5)*pitch  # [m]
    y = (np.arange(0, n_y) - n_y//2 + 0.5)*pitch  # [m]
    x = np.atleast_2d(x)  # (1, n_x)
    y = np.atleast_2d(y).T  # (n_y, 1)
    # Assuming all elements are at z = 0
    z = np.zeros((n_y, n_x), dtype=np.float64)
    delays = []
    center_delays = []
    for f, ang_zx, ang_zy in zip(tx_focus, tx_ang_zx, tx_ang_zy):
        # Convert plane inclinations to the spherical angles
        ang_zenith = np.hypot(np.tan(ang_zx), np.tan(ang_zy))
        ang_zenith = np.arctan(ang_zenith)
        ang_azimuth = np.arctan2(np.tan(ang_zy), np.tan(ang_zx))
        # NOTE: for moving TX aperture, tx centers are needed.
        if np.isinf(f):
            # Plane wave transmission
            tx_dist = z*np.cos(ang_zenith) \
                      + (x*np.cos(ang_azimuth)+y*np.sin(ang_azimuth)) \
                        * np.sin(ang_zenith)
            tx_center_distance = 0  # Note: this will not work with the moving
                                    # aperture.
        else:
            # Note: assuming the center of aperture in (0, 0, 0)
            z_foc = f*np.cos(ang_zenith)
            x_foc = f*np.sin(ang_zenith)*np.cos(ang_azimuth)  # + tx ap center x
            y_foc = f*np.sin(ang_zenith)*np.sin(ang_azimuth)  # + tx ap center y
            tx_dist = np.sqrt((x_foc-x)**2 + (y_foc-y)**2 + (z_foc-z)**2)
            tx_center_distance = 0
        delay = tx_dist/speed_of_sound
        if f > 0:
            delay = delay*(-1)
        center_delay = tx_center_distance/speed_of_sound
        delays.append(delay)
        center_delays.append(center_delay)
    delays = np.stack(delays)
    center_delays = np.stack(center_delays)

    min_delays = np.min(delays)
    delays = delays-min_delays
    center_delays = center_delays-min_delays

    tx_center_delay = np.max(center_delays)
    for i in range(delays.shape[0]):
        delays[i] = delays[i] - center_delays[i] + tx_center_delay
    delays = np.delete(delays, (n_x // 4, n_x // 2 + 1, 3 * n_x // 4 + 2),
        axis=1)
    return delays.flatten()


def get_sequence(probe_model, tx_focus, tx_ang_zx, tx_ang_zy):
    n_elements = probe_model.n_elements
    delays = get_delays(
        tx_focus=tx_focus,
        tx_ang_zx=tx_ang_zx,
        tx_ang_zy=tx_ang_zy,
        probe_model=probe_model,
        speed_of_sound=speed_of_sound
    )
    ops = [TxRx(
        tx=Tx(
            aperture=[True] * n_elements,  # Full TX aperture.
            excitation=Pulse(center_frequency=tx_frequency, n_periods=n_periods,
                             inverse=False),
            delays=d
        ),
        rx=Rx(
            # Full RX aperture
            # (note: will be automatically splitted to multiple RX sub-aperts.)
            aperture=[True] * n_elements,
            sample_range=(0, n_samples)
        ),
        pri=1 / prf
    ) for d in delays]
    return TxRxSequence(
        ops=ops,
        tgc_curve=np.ndarray([]),  # Will be set later.
        # How many times this sequence should be repeated before
        # starting data transfer from us4R to host PC.
        n_repeats=1,
    )


def get_imaging(sequence, tx_focus, tx_ang_zx, tx_ang_zy,
                n_last_frames_to_save=10):
    z_grid_size = len(z_grid)
    x_grid_size = len(x_grid)
    y_grid_size = len(y_grid)
    rf_queue = deque(maxlen=n_last_frames_to_save)

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
                tx_foc=tx_focus,
                tx_ang_zx=tx_ang_zx, tx_ang_zy=tx_ang_zy,
                speed_of_sound=speed_of_sound),
            Squeeze(),
            EnvelopeDetection(),
            LogCompression(),
            # Get the center
            Lambda(
                lambda data: cp.concatenate((
                    data[y_grid_size // 2, :, :].T,
                    data[:, x_grid_size // 2:, :].T), axis=1),
                lambda metadata: metadata.copy(
                    input_shape=(z_grid_size, x_grid_size + y_grid_size))),
        ),
        placement="/GPU:0"
    )
    return pipeline, rf_queue