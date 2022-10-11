"""
This script acquires and reconstructs RF img for plane wave imaging
(synthetic aperture) and applies NNBmode imaging for despeckling.

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
    Mean
)
from arrus.utils.us4r import (
    RemapToLogicalOrder
)
from arrus.utils.gui import (
    Display2D
)

import tensorflow as tf

gpus = tf.config.experimental.list_physical_devices('GPU')
for gpu in gpus:
    tf.config.experimental.set_memory_growth(gpu, True)


arrus.set_clog_level(arrus.logging.INFO)
arrus.add_log_file("test.log", arrus.logging.INFO)


def main(model_weights):

    seq = PwiSequence(
        angles=np.linspace(-5, 5, 7)*np.pi/180,
        pulse=Pulse(center_frequency=8e6, n_periods=2, inverse=False),
        rx_sample_range=(0, 2048),
        downsampling_factor=2,
        speed_of_sound=1450,
        pri=200e-6,
        sri=50e-3,
        tgc_start=14,
        tgc_slope=2e2)

    display_input_queue = queue.Queue(1)

    x_grid = np.arange(-15, 15, 0.1) * 1e-3
    z_grid = np.arange(5, 45, 0.1) * 1e-3
    extent = np.array([np.min(x_grid), np.max(x_grid), np.max(z_grid), np.min(z_grid)])*1e3

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
                Transpose(axes=(0, 2, 1)),
                FirFilter(taps=taps),
                QuadratureDemodulation(),
                Decimation(decimation_factor=1, cic_order=2),
                ReconstructLri(x_grid=x_grid, z_grid=z_grid),
                ApplyNNBmode(model_weights),
                Enqueue(display_input_queue, block=False, ignore_full=True)
            ),
            placement="/GPU:0"
        )
    )

    # Here starts communication with the device.
    with arrus.Session("/home/nvidia/us4r.prototxt") as sess:
        us4r = sess.get_device("/Us4R:0")
        us4r.set_hv_voltage(50)

        # Upload sequence on the us4r-lite device.
        buffer, const_metadata = sess.upload(scheme)
        display = Display2D(const_metadata, value_range=(90, 160), cmap="gray",
                            title="NNBmode", xlabel="Azimuth (mm)", ylabel="Depth (mm)",
                            show_colorbar=True, extent=extent)
        sess.start_scheme()
        display.start(display_input_queue)
        print("Display closed, stopping the script.")

    # When we exit the above scope, the session and scheme is properly closed.
    print("Stopping the example.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="NNBmode despeckling example. The example uses PWI scheme.")
    parser.add_argument("--model_weights", dest="model_weights",
                        help="Start sample", required=True)
    args = parser.parse_args()
    main(args.model_weights)
