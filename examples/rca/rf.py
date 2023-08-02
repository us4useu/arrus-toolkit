import arrus
import arrus.session
import arrus.utils.imaging
import arrus.utils.us4r
from collections import deque
import numpy as np

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


def main():
    # Here starts communication with the device.
    with arrus.Session("./us4r.prototxt") as sess:
        center_frequency = 6e6
        tx_voltage = 20
        us4r = sess.get_device("/Us4R:0")
        us4r.set_hv_voltage(tx_voltage)

        n_elements = us4r.get_probe_model().n_elements

        rows = np.zeros(n_elements).astype(bool)
        columns = np.zeros(n_elements).astype(bool)
        columns[:128] = True
        rows[128:] = True

        tx_aperture = rows
        tx_aperture_size = np.sum(tx_aperture)
        rx_aperture = columns
        seq = TxRxSequence(
            ops=[
                TxRx(
                    Tx(aperture=tx_aperture,
                       excitation=Pulse(center_frequency=center_frequency,
                                        n_periods=2, inverse=False),
                       delays=[0]*tx_aperture_size),
                    Rx(aperture=rx_aperture,
                       sample_range=(0, 4096),
                       downsampling_factor=1),
                    pri=200e-6
                ),
            ],
            # Turn off TGC.
            tgc_curve=[34]*24,  # [dB]
            # Time between consecutive acquisitions, i.e. 1/frame rate.
            sri=20e-3
        )
        # Declare the complete scheme to execute on the devices.
        data_queue = deque(maxlen=(10))
        scheme = Scheme(
            # Run the provided sequence.
            tx_rx_sequence=seq,
            # Processing pipeline to perform on the GPU device.
            processing=Pipeline(
                steps=(
                    Lambda(
                        lambda data: (data_queue.append(data.get()), data)[1]),
                    RemapToLogicalOrder(),
                    Squeeze(),
                ),
                placement="/GPU:0"
            )
        )
        # Upload the scheme on the us4r-lite device.
        buffer, metadata = sess.upload(scheme)
        # Created 2D image display.
        display = Display2D(metadata=metadata, value_range=(-100, 100), cmap="viridis")
        input("Are you sure you want to continue?")
        # Start the scheme.
        sess.start_scheme()
        # Start the 2D display.
        # The 2D display will consume data put the the input queue.
        # The below function blocks current thread until the window is closed.
        display.start(buffer)
        np.save("data.npy", np.stack(data_queue))
        print("Display closed, stopping the script.")

    # When we exit the above scope, the session and scheme is properly closed.
    print("Stopping the example.")


if __name__ == "__main__":
    main()
