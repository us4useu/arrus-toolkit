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
    z_grid = np.arange(0e-3, 40e-3, 0.1e-3)

    # Initial TGC curve.
    tgc_sampling_points = np.linspace(np.min(z_grid), np.max(z_grid), 10)
    tgc_values = np.linspace(14, 54, 10)

    sequence = StaSequence(
        tx_aperture_center=np.linspace(-15, 15, 11)*1e-3,
        tx_aperture_size=32,
        tx_focus=-6e-3,
        pulse=Pulse(center_frequency=6e6, n_periods=2, inverse=False),
        rx_depth_range=get_depth_range(z_grid),
        speed_of_sound=medium.speed_of_sound,
        pri=200e-6
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
        voltage=5
    )


ENV = UltrasoundEnv(
    session_cfg="/home/pjarosik/us4r.prototxt",
    configure=configure,
)
