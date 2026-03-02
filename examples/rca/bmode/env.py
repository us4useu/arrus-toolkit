import arrus.medium
import numpy as np
from arrus.ops.imaging import *
from arrus.ops.us4r import *
from arrus.utils.imaging import *
from arrus_rca_utils.reconstruction import (
    get_reconstruction_graph
)
from arrus_rca_utils.sequence import (
    get_pw_sequence
)
from gui4us.model.envs.arrus import (
    UltrasoundEnv,
    ArrusEnvConfiguration,
    Curve
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

    n_samples = 4*1024

    medium = arrus.medium.Medium(name="ats560h", speed_of_sound=1480)
    angles = np.linspace(-10, 10, 32) * np.pi / 180  # [rad]
    center_frequency = 6e6  # [Hz]
    n_periods = 2
    sample_range = (0, n_samples)
    pri = 400e-6

    # Imaging grid.
    y_grid = np.arange(-10e-3, 10e-3, 0.2e-3)
    x_grid = np.arange(-10e-3, 10e-3, 0.2e-3)
    z_grid = np.arange(5e-3, 45e-3, 0.2e-3)

    common_parameters = dict(
        medium=medium,
        angles=angles,
        n_periods=n_periods,
        center_frequency=center_frequency,
        sample_range=sample_range,
        pri=pri,
    )
    # Initial TGC curve.
    tgc_sampling_points = np.linspace(np.min(z_grid), np.max(z_grid), 10)
    tgc_values = np.linspace(34, 54, 10)

    # TX with OX elements, RX with OY elements
    sequence_xy = get_pw_sequence(
        array_tx_id=array_ox_id,
        array_tx=array_ox,
        array_rx_id=array_oy_id,
        array_rx=array_oy,
        name="XY",
        **common_parameters
    )
    # TX with OY elements, RX with OX elements
    sequence_yx = get_pw_sequence(
        array_tx_id=array_oy_id,
        array_tx=array_oy,
        array_rx_id=array_ox_id,
        array_rx=array_ox,
        name="YX",
        **common_parameters
    )

    fir_taps = scipy.signal.firwin(
        numtaps=64, cutoff=np.array([0.5, 1.5]) * center_frequency,
        pass_zero="bandpass", fs=us4r.current_sampling_frequency
    )

    reconstruction = get_reconstruction_graph(
        fir_taps=fir_taps,
        x_grid=x_grid, y_grid=y_grid, z_grid=z_grid,
        sequence_xy=sequence_xy,
        sequence_yx=sequence_yx,
        slice=True
    )
    scheme = Scheme(
        tx_rx_sequence=[
            sequence_xy,
            sequence_yx
        ],
        processing=reconstruction
    )
    return ArrusEnvConfiguration(
        medium=medium,
        scheme=scheme,
        tgc=Curve(points=tgc_sampling_points, values=tgc_values),
        voltage=40
    )


ENV = UltrasoundEnv(
    session_cfg=str(Path(__file__).parent / "us4r.prototxt"),
    configure=configure,
)
