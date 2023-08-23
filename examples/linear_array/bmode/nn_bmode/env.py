import arrus.medium
import arrus.logging
from arrus.utils.imaging import *
from arrus.ops.imaging import *
from arrus.ops.us4r import *
from gui4us.model.envs.arrus import (
    UltrasoundEnv,
    ArrusEnvConfiguration,
    Curve,
    get_depth_range
)
from ops import ApplyNNBmode
import urllib.request
import tempfile


def download_model():
    filepath = os.path.join(os.path.dirname(__file__), "model.h5")
    if not os.path.exists(filepath):  # for the sake of simplicity
        arrus.logging.log(arrus.logging.INFO, "Downloading model weights...")
        urllib.request.urlretrieve(
            "https://gitlab.com/dongwoon.hyun/nn_bmode/-/raw/master/runs/pretrained/model.h5?inline=false",
            filepath)
        arrus.logging.log(
            arrus.logging.INFO, f"... downloaded to path: {filepath}.")
    return filepath


def configure(session: arrus.Session):
    model_filepath = download_model()
    medium = arrus.medium.Medium(name="ats549", speed_of_sound=1450)
    probe_model = session.get_device("/Us4R:0").get_probe_model()
    # Imaging grid.
    x_grid = np.arange(probe_model.x_min, probe_model.x_max, 0.1e-3)
    z_grid = np.arange(5e-3, 45e-3, 0.1e-3)

    # Initial TGC curve.
    tgc_sampling_points = np.linspace(np.min(z_grid), np.max(z_grid), 10)
    tgc_values = np.linspace(14, 54, 10)

    sequence = PwiSequence(
        angles=np.linspace(-10, 10, 7) * np.pi / 180,
        pulse=Pulse(center_frequency=8e6, n_periods=2, inverse=False),
        rx_depth_range=get_depth_range(z_grid+5e-3),
        speed_of_sound=medium.speed_of_sound,
        pri=150e-6,
    )
    pipeline = Pipeline(
        steps=(
            # Channel data pre-processing.
            RemapToLogicalOrder(),
            Transpose(axes=(0, 1, 3, 2)),
            BandpassFilter(),
            QuadratureDemodulation(),
            Decimation(decimation_factor=4, cic_order=2),
            ReconstructLri(x_grid=x_grid, z_grid=z_grid),
            Pipeline(
                steps=(
                    ApplyNNBmode(model_filepath),
                    Transpose()
                ),
                placement="/GPU:0"
            ),
            Mean(axis=1),
            EnvelopeDetection(),
            Mean(axis=0),
            Transpose(),
            LogCompression()
        ),
        placement="/GPU:0")

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
    session_cfg="/home/public/us4r.prototxt",
    configure=configure,
)
