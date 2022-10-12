import pickle

import arrus
import arrus.session
import arrus.utils.imaging
import arrus.utils.us4r
from collections import deque
import numpy as np
import cupy as cp
import pathlib
import os.path
from arrus.utils.imaging import *
from tools import Hilbert
from parameters_rf import *
import cupyx.scipy.ndimage

from arrus.ops.us4r import (
    Scheme,
    Pulse,
    Tx,
    Rx,
    TxRx,
    TxRxSequence
)
from arrus.utils.imaging import (
    Pipeline,
    SelectFrame,
    Squeeze,
    Lambda,
    RemapToLogicalOrder,
    SelectSequence
)
from arrus.utils.gui import (
    Display2D
)

arrus.set_clog_level(arrus.logging.INFO)
arrus.add_log_file("test.log", arrus.logging.INFO)

pitch = 0.2e-3  # [m]
n_elements_single_axis = 128
total_n_elements = 2*n_elements_single_axis
c = 1450  # [m/s]
center_frequency = 6e6
tx_voltage = 30
n_samples = 4096
angles = np.linspace(-10, 10, 16)*np.pi/180

rows = np.zeros(total_n_elements).astype(bool)
columns = np.zeros(total_n_elements).astype(bool)
columns[:n_elements_single_axis] = True
rows[n_elements_single_axis:] = True


def main():
    # Here starts communication with the device.
    with arrus.Session("./us4r.prototxt") as sess:
        us4r = sess.get_device("/Us4R:0")
        us4r.set_hv_voltage(tx_voltage)
        seq = get_sequence(angles)
        # Declare the complete scheme to execute on the devices.
        data_queue = deque(maxlen=(10))
        speed_of_sound = 1450
        downsampling_factor = 1
        fs = 65e6
        sampling_time = np.arange(start=round(400/downsampling_factor),
                                  stop=n_samples,
                                  step=round(150/downsampling_factor))/fs
        tgc_start = 14
        tgc_slope = 2.2e2
        distance = sampling_time*speed_of_sound
        tgc_values = tgc_start + distance*tgc_slope
        us4r.set_tgc(tgc_values)

        x_grid = np.arange(-5, 5, 0.2)*1e-3  # [m]
        y_grid = np.arange(-5, 5, 0.2)*1e-3  # [m]
        z_grid = np.arange(5, 55, 0.1)*1e-3  # [m]

        downsampling_factor = 1
        taps = scipy.signal.firwin(
            numtaps=31,
            cutoff=[0.5*center_frequency, 1.5*center_frequency],
            pass_zero=False,
            fs=65e6/downsampling_factor
        )
        rca_rec = ReconstructRCA(output_grid=(x_grid, y_grid, z_grid),
                         angles=angles,
                         speed_of_sound=c)
        q = deque(maxlen=10)
        scheme = Scheme(
            # Run the provided sequence.
            tx_rx_sequence=seq,
            # Processing pipeline to perform on the GPU device.
            processing=Pipeline(
                steps=(
                    # RF processing
                    RemapToLogicalOrder(),
                    Transpose(axes=(0, 1, 3, 2)),
                    FirFilter(taps),
                    rca_rec,
                    Mean(axis=0),
                    Hilbert(),
                    EnvelopeDetection(),
                    Lambda(lambda data: data/cp.nanmax(data)),
                    LogCompression(),
                    Lambda(lambda data: data+30.0),
                    # # Envelope compounding
                    Transpose((2, 0, 1)),  # (y, x, z) -> (z, y, x)
                    # LogCompression(),
                    Lambda(
                        lambda data: cp.concatenate((
                            data[:-100:2, len(y_grid)//2, :],
                            data[:-100:2, :, len(x_grid)//2]), axis=1),
                        lambda metadata: metadata.copy(input_shape=((len(z_grid)-100)//2, len(x_grid)+len(y_grid)))),
                    Lambda(lambda data: cupyx.scipy.ndimage.median_filter(data, size=5)),
                    DynamicRangeAdjustment(0, 30),
                    Lambda(lambda data: (print(f"{np.min(data)} {np.max(data)}"), data)[1]),
                ),
                placement="/GPU:0"
            )
        )
        # Upload the scheme on the us4r-lite device.
        buffer, metadata = sess.upload(scheme)
        # Created 2D image display.
        display = Display2D(metadata=metadata, value_range=(0, 30), cmap="gray",
                            title="B-mode",
                            show_colorbar=True, input_timeout=30)

        # display = Display2D(metadata=metadata, value_range=(0, 50000),
        #                     extent=[0, 64, 50, 0], cmap="viridis")
        input("Are you sure you want to continue?")
        # Start the scheme.
        sess.start_scheme()
        # Start the 2D display.
        # The 2D display will consume data put the the input queue.
        # The below function blocks current thread until the window is closed.
        display.start(buffer)
        pickle.dump({"rf": np.stack(q), "metadata": metadata}, open("data.pkl", "wb"))
        np.save("data.npy", np.stack(q))
        print("Display closed, stopping the script.")

    # When we exit the above scope, the session and scheme is properly closed.
    print("Stopping the example.")


if __name__ == "__main__":
    main()
