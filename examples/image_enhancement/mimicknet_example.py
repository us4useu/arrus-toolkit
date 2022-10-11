"""
This script acquires and reconstructs RF img for plane wave imaging
(synthetic aperture) and applies MimickNet model for despeckling.

GPU usage is recommended.
"""

import scipy.signal
import arrus
import arrus.session
import arrus.utils.imaging
import arrus.utils.us4r
import numpy as np
import queue
import time
from nn_bmode.external import ApplyNNBmode
import argparse

from arrus.ops.us4r import (
    Scheme,
    Pulse,
    DataBufferSpec
)
from arrus.ops.imaging import (
    PwiSequence
)
from arrus.utils.imaging import (
    Pipeline,
    Transpose,
    BandpassFilter,
    FirFilter,
    Decimation,
    QuadratureDemodulation,
    EnvelopeDetection,
    LogCompression,
    Enqueue,
    RxBeamformingImg,
    ReconstructLri,
    Sum,
    Lambda,
    Squeeze
)
from arrus.utils.us4r import (
    RemapToLogicalOrder
)
from arrus.utils.gui import (
    Display2D
)

from utilities import RunForDlPackCapsule, Reshape

import tensorflow as tf
import cupy as cp

gpus = tf.config.experimental.list_physical_devices('GPU')
for gpu in gpus:
    tf.config.experimental.set_memory_growth(gpu, True)


arrus.set_clog_level(arrus.logging.INFO)
arrus.add_log_file("test.log", arrus.logging.INFO)


def main(model_weights):
    seq = PwiSequence(
        angles=np.linspace(-5, 5, 7)*np.pi/180,
        pulse=Pulse(center_frequency=6e6, n_periods=2, inverse=False),
        rx_sample_range=(0, 2048),
        downsampling_factor=2,
        speed_of_sound=1450,
        pri=200e-6,
        sri=20e-3,
        tgc_start=14,
        tgc_slope=2e2)

    display_input_queue = queue.Queue(1)

    x_grid = np.arange(-15, 15, 0.1) * 1e-3
    z_grid = np.arange(5, 45, 0.1) * 1e-3
    x_size = len(x_grid)
    z_size = len(z_grid)
    extent = np.array([np.min(x_grid), np.max(x_grid), np.max(z_grid), np.min(z_grid)])*1e3

    model = tf.keras.models.load_model(model_weights)
    model.predict(np.zeros((1, z_size, x_size, 1), dtype=np.float32))

    def normalize(img):
        data = img-cp.min(img)
        data = data/cp.max(data)
        return data

    def mimicknet_predict(capsule):
        data = tf.experimental.dlpack.from_dlpack(capsule)
        result = model.predict_on_batch(data)

        # Compensate a large variance of the image mean brightness.
        result = result-np.mean(result)
        result = result-np.min(result)
        result = result/np.max(result)
        return result

    # taps for fir filter definition 
    fs = 65e6
    taps= scipy.signal.firwin(32, 
            np.array([0.7, 1.7])*seq.pulse.center_frequency,
            pass_zero=False, 
            fs=fs)

    scheme = Scheme(
        tx_rx_sequence=seq,
        rx_buffer_size=2,
        output_buffer=DataBufferSpec(type="FIFO", n_elements=4),
        work_mode="HOST",
        processing=Pipeline(
            steps=(
                RemapToLogicalOrder(),
                Transpose(axes=(0, 1, 3, 2)),
                # BandpassFilter(bounds=(0.5,1.5)),
                FirFilter(taps=taps),
                QuadratureDemodulation(),
                Decimation(decimation_factor=2, cic_order=2),
                ReconstructLri(x_grid=x_grid, z_grid=z_grid),
                Sum(axis=0),
                EnvelopeDetection(),
                Transpose(),
                LogCompression(),
                # MimickNet preprocessing
                Lambda(normalize),
                Reshape(shape=(1, z_size, x_size, 1)),
                # MimickNet
                RunForDlPackCapsule(mimicknet_predict),
                Squeeze(),
            ),
            placement="/GPU:0"
        )
    )

    # Here starts communication with the device.
    with arrus.Session("./us4r.prototxt") as sess:
        us4r = sess.get_device("/Us4R:0")
        us4r.set_hv_voltage(20)

        # Upload sequence on the us4r-lite device.
        buffer, const_metadata = sess.upload(scheme)
        display = Display2D(const_metadata, cmap="gray", value_range=(0.3, 1),
                            title="MimickNet", xlabel="Azimuth (mm)", ylabel="Depth (mm)",
                            show_colorbar=True, extent=extent)
        sess.start_scheme()
        display.start(display_input_queue)
        print("Display closed, stopping the script.")

    # When we exit the above scope, the session and scheme is properly closed.
    print("Stopping the example.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="MimickNet despeckling example. The example uses PWI scheme.")
    parser.add_argument("--model_weights", dest="model_weights",
                        help="Start sample", required=True)
    args = parser.parse_args()
    main(args.model_weights)
