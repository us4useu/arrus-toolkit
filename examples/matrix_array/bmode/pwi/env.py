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
from imaging import get_sequence, get_imaging, get_rf_imaging, get_rf_imaging_flat


def configure(session: arrus.Session):
    us4r = session.get_device("/Us4R:0")
    medium = arrus.medium.Medium(name="ats560h", speed_of_sound=1450)
    angles_oxz = np.linspace(-10, 10, 7) * np.pi / 180  # [rad]
    angles_oyz = np.zeros(len(angles_oxz))
    tx_focus = np.zeros(len(angles_oxz))
    tx_focus[:] = np.inf

    center_frequency = 3e6  # [Hz]
    n_periods = 2
    sample_range = (0, 1024)
    # sample_range = (0, 4*1024)
    pri = 400e-6
    # Imaging grid.
    y_grid = np.arange(-8e-3, 8e-3, 0.2e-3)
    x_grid = np.arange(-8e-3, 8e-3, 0.2e-3)
    z_grid = np.arange(10e-3, sample_range[1]/us4r.sampling_frequency*medium.speed_of_sound, 0.2e-3)

    # Initial TGC curve.
    tgc_sampling_points = np.linspace(np.min(z_grid), np.max(z_grid), 10)
    tgc_values = np.linspace(14, 54, 10)

    sequence = get_sequence(
        probe_model=us4r.get_probe_model(),
        speed_of_sound=medium.speed_of_sound,
        tx_focus=tx_focus,
        tx_ang_zx=angles_oxz,
        tx_ang_zy=angles_oyz,
        tx_frequency=center_frequency,
        n_periods=n_periods,
        sample_range=sample_range,
        pri=pri
    )
    imaging = get_imaging(
        sequence=sequence,
        speed_of_sound=medium.speed_of_sound,
        tx_focus=tx_focus, tx_ang_zx=angles_oxz, tx_ang_zy=angles_oyz,
        x_grid=x_grid, y_grid=y_grid, z_grid=z_grid,
        decimation_factor=30, decimation_fir_order=64,
        sample_range=sample_range,
    )
    # imaging = get_rf_imaging(
    #     sample_range=sample_range
    # )
    # imaging = get_rf_imaging_flat()

    scheme = Scheme(
        tx_rx_sequence=sequence,
        processing=imaging
    )
    return ArrusEnvConfiguration(
        medium=medium,
        scheme=scheme,
        tgc=Curve(points=tgc_sampling_points, values=tgc_values),
        voltage=10
    )


ENV = UltrasoundEnv(
    session_cfg=str(Path(__file__).parent / "us4r.prototxt"),
    configure=configure,
)
