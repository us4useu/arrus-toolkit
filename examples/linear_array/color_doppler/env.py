import arrus.medium
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


def configure(session: arrus.Session):
    medium = arrus.medium.Medium(name="human_carotid", speed_of_sound=1540)
    probe_model = session.get_device("/Us4R:0").get_probe_model()
    # Imaging grid.
    x_grid = np.arange(probe_model.x_min, probe_model.x_max, 0.1e-3)
    z_grid = np.arange(0e-3, 20e-3, 0.1e-3)

    # Initial TGC curve.
    tgc_sampling_points = np.linspace(np.min(z_grid), np.max(z_grid), 10)
    tgc_values = np.linspace(54, 54, 10)

    # Doppler sequence.
    center_frequency = 6e6
    doppler_angle = 10  # [deg]
    n_angles = 64
    doppler_sequence = PwiSequence(
        angles=np.array([doppler_angle * np.pi / 180]),
        pulse=Pulse(center_frequency=center_frequency, n_periods=2,
                    inverse=False),
        rx_sample_range=(0, 2*1024),
        speed_of_sound=medium.speed_of_sound,
        pri=100e-6,
        n_repeats=n_angles
    )

    # Pipeline.
    pipeline = Pipeline(
        steps=(
            RemapToLogicalOrder(),
            Transpose(axes=(0, 1, 3, 2)),
            BandpassFilter(),
            QuadratureDemodulation(),
            Decimation(decimation_factor=4, cic_order=2),
            ReconstructLri(x_grid=x_grid, z_grid=z_grid),
            Squeeze(),
            Pipeline(
                # -> Color Doppler.
                steps=(
                    FilterWallClutter(w_n=0.3, n=64),
                    ReconstructDoppler(),
                    Transpose(axes=(0, 2, 1)),
                    CreateDopplerFrame(
                        color_dynamic_range=(-30, 30),
                        power_dynamic_range=(0, 80))
                ),
                placement="/GPU:0"
            ),
            # -> B-mode. Take the last PW frame to create the background B-mode
            # image.
            SelectFrames([n_angles-1]),
            Squeeze(),
            EnvelopeDetection(),
            Transpose(),
            LogCompression(),
        ),
        placement="/GPU:0"
    )
    return ArrusEnvConfiguration(
        medium=medium,
        scheme=arrus.ops.us4r.Scheme(
            tx_rx_sequence=doppler_sequence,
            processing=pipeline
        ),
        tgc=Curve(points=tgc_sampling_points, values=tgc_values),
        voltage=5
    )


ENV = UltrasoundEnv(
    session_cfg="/home/pjarosik/us4r.prototxt",
    configure=configure,
)
