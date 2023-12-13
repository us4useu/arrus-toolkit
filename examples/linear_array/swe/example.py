"""
This script acquires and reconstructs RF img for plane wave imaging
(synthetic aperture).

GPU usage is recommended.
"""

import arrus
import arrus.session
import arrus.utils.imaging
import arrus.utils.us4r
import queue
import numpy as np
import arrus.ops.tgc
import arrus.medium

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
    SelectFrames,
    Squeeze,
    Lambda,
    RemapToLogicalOrder
)
from arrus.utils.gui import (
    Display2D
)

arrus.set_clog_level(arrus.logging.INFO)
arrus.add_log_file("swe_test.log", arrus.logging.TRACE)


def main():
    # Here starts communication with the device.
    medium = arrus.medium.Medium(name="water", speed_of_sound=1490)
    with arrus.Session("C:/Users/Public/us4r.prototxt", medium=medium) as sess:
        us4r = sess.get_device("/Us4R:0")
        us4r.set_hv_voltage(5)

        n_elements = us4r.get_probe_model().n_elements
        n_samples = 4096
        tx_frequency = 6e6
        push_pulse_length = 1e-6  # [s]
        push_pulse_n_periods = push_pulse_length*tx_frequency
        print(push_pulse_n_periods)
        # Make sure a single TX/RX for the push sequence will be applied.
        push_rx_aperture = [False]*n_elements
        push_sequence = [
            TxRx(
                # NOTE: full transmit aperture.
                Tx(aperture=[True]*n_elements,
                    excitation=Pulse(center_frequency=tx_frequency, n_periods=push_pulse_n_periods, inverse=False),
                    focus=20e-3,  # [m]
                    angle=0,  # [rad]
                    speed_of_sound=medium.speed_of_sound
                ),
                Rx(
                    aperture=push_rx_aperture,
                    sample_range=(0, n_samples),
                    downsampling_factor=1
                ),
                pri=1e-3
            )
        ]
        imaging_pw_n_repeats = 2
        imaging_sequence = [
            TxRx(
                Tx(
                    aperture=[True]*n_elements,
                    excitation=Pulse(center_frequency=tx_frequency, n_periods=2, inverse=False),
                    focus=np.inf,  # [m]
                    angle=0,  # [rad]
                    speed_of_sound=medium.speed_of_sound
                ),
                Rx(
                    aperture=[True]*n_elements,
                    sample_range=(0, n_samples),
                    downsampling_factor=1
                ),
                pri=200e-6
            )
            ]*imaging_pw_n_repeats

        seq = TxRxSequence(ops=push_sequence+imaging_sequence)
        # Declare the complete scheme to execute on the devices.
        scheme = Scheme(
            # Run the provided sequence.
            tx_rx_sequence=seq,
            # Processing pipeline to perform on the GPU device.
            processing=Pipeline(
                steps=(
                    RemapToLogicalOrder(),
                    Squeeze(),
                    # SelectFrames([0]),
                    Squeeze(),
                ),
                placement="/GPU:0"
            )
        )
        # Upload the scheme on the us4r-lite device.
        buffer, metadata = sess.upload(scheme)
        us4r.set_tgc(arrus.ops.tgc.LinearTgc(start=34, slope=2e2))
        # Created 2D image display.
        display = Display2D(metadata=metadata, value_range=(-100, 100))
        # Start the scheme.
        # sess.start_scheme()  # TODO
        # Start the 2D display.
        # The 2D display will consume data put the the input queue.
        # The below function blocks current thread until the window is closed.
        # display.start(buffer)  # TODO
        print("Display closed, stopping the script.")

    # When we exit the above scope, the session and scheme is properly closed.
    print("Stopping the example.")


if __name__ == "__main__":
    main()
