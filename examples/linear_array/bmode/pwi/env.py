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


def configure(session: arrus.Session):
    medium = arrus.medium.Medium(name="ats549", speed_of_sound=1450)
    probe_model = session.get_device("/Us4R:0").get_probe_model()
    # Imaging grid.
    x_grid = np.arange(probe_model.x_min, probe_model.x_max, 0.1e-3)
    z_grid = np.arange(5e-3, 60e-3, 0.1e-3)

    # Initial TGC curve.
    tgc_sampling_points = np.linspace(np.min(z_grid), np.max(z_grid)+6e-3, 10)
    tgc_values = np.linspace(14, 54, 10)

    sequence = PwiSequence(
        angles=np.linspace(-10, 10, 32) * np.pi / 180,
        pulse=Pulse(center_frequency=6e6, n_periods=2, inverse=False),
        rx_depth_range=get_depth_range(z_grid),
        speed_of_sound=medium.speed_of_sound,
        pri=200e-6,
    )
    pipeline = get_bmode_imaging(
        sequence=sequence,
        grid=(x_grid, z_grid),
    )
    return ArrusEnvConfiguration(
        medium=medium,
        scheme=arrus.ops.us4r.Scheme(
            tx_rx_sequence=sequence,
            processing=pipeline
        ),
        tgc=Curve(points=tgc_sampling_points, values=tgc_values),
        voltage=20
    )


ENV = UltrasoundEnv(
    session_cfg="/home/public/us4r.prototxt",
    configure=configure,
)
