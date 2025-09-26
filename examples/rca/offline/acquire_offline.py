# import libs....
#
## we use the acquire function to acquire data bypassing the GUI with a given
## parameters configuration and to save the IQ beamformed data as .mat files

from collections import deque
import time
import pickle
import threading
import sys
from utils import *


class CaptureBuffer:

    def __init__(self, size):
        self.buffer = []
        self.size = size
        self.buffer_is_ready = threading.Event()
        self.last_time = time.time()

    def callback(self, element):
        now = time.time()
        dt = now-self.last_time
        print(f"Data size: {element.data.nbytes/(2**20)} MiB", end="\r")

        if len(self.buffer) < self.size:
            # Copy the numpy array to the buffer
            self.buffer.append(element.data.copy())
        elif len(self.buffer) == self.size:
            # If this is the last element in the buffer -- unblock the main thread.
            self.buffer_is_ready.set()
        element.release()

    def wait_for_data(self):
        self.buffer_is_ready.wait()

    def get_data(self):
        return np.stack(self.buffer)


def acquire(medium, nframes, batch_size, sequence_xy, sequence_yx, tgc_sampling_points, tgc_values, voltage,
            system_buffer_size=4):
    """
    Acquires raw RF data (before IQ demodulation) for the given parameters (sequences, medium, etc.).
    
    :return: pair: capture buffer (with the RF data) and ARRUS metadata
    """
    
    if nframes % batch_size != 0:
        raise ValueError("n frames should be a multiple of batch size")


    with arrus.Session("6464.prototxt", medium=medium) as sess:
        us4r = sess.get_device("/Us4R:0")
        us4r.set_hv_voltage(voltage)

        system_sequence = get_system_sequence(
            sequence_xy=sequence_xy,
            sequence_yx=sequence_yx,
            probe_model=us4r.get_probe_model(),
            device_sampling_frequency=us4r.current_sampling_frequency,
        )

        system_sequence = dataclasses.replace(system_sequence, n_repeats=batch_size)

        scheme = Scheme(
            tx_rx_sequence=system_sequence,
            rx_buffer_size=system_buffer_size,
            output_buffer=DataBufferSpec(type="FIFO", n_elements=system_buffer_size),
            work_mode="SYNC"
        )

        # Upload the scheme on the us4r-lite device.
        device_buffer, metadata = sess.upload(scheme)
        capture_buffer = CaptureBuffer(size=nframes / batch_size)
        device_buffer.append_on_new_data_callback(capture_buffer.callback)
        us4r.set_tgc((tgc_sampling_points, tgc_values))

        sess.start_scheme()
        capture_buffer.wait_for_data()
        sess.stop_scheme()
        return capture_buffer, metadata


def get_time_intervals(data, metadata):
    """
    This function determines the actual time intervals (frame rate) between BATCHES, i.e. scales it properly
    according to the given number of batches.

    :return: returns pair: beamformed HRIs and the time intervals between consecutive frames (calculated per batch)
    """
    batch_size = metadata.context.sequence.n_repeats
    start_sample, end_sample = metadata.context.sequence.ops[0].rx.sample_range
    n_samples = end_sample-start_sample
    fs = metadata.context.device.sampling_frequency

    n_frames, total_n_samples, n_channels = data.shape

    # Determine timestamps -> time intervals.
    frame_metadata = data[..., 0, 4:8]
    timestamps = frame_metadata.view(np.uint64).copy()
    timestamps = timestamps.flatten()
    timestamps = timestamps / fs
    timestamps = timestamps.squeeze()
    time_intervals = np.diff(timestamps)
    return time_intervals / batch_size


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise ValueError("The name of the output dataset should be provided")

    name = sys.argv[1].strip()
    medium           =  arrus.medium.Medium(name="tissue", speed_of_sound=1450)
    angles           =  np.linspace(-20, 20, 9) * np.pi / 180  # [rad] NUMBER OF PLANE WAVES FOR COMPOUNDING
    center_frequency =  7e6  # [Hz]
    n_periods        =  12
    sample_range     =  (0, 8*1024)  # @65 MHz  #5*1024  # DEPTH DIMENSION
    pri              =  240e-6 # 119e-6
    batch_size       =  14
    nframes          =  batch_size*10  # Should be a multiple of batch_size.
    voltage          =  5
    
    
    # Image reconstruction parameters.
    y_grid = np.arange(-10e-3, 10e-3, 0.2e-3)  # np.arange(-10e-3, 10e-3, 0.2e-3)  ###### VOLUME of VIEW
    x_grid = np.arange(-10e-3, 10e-3, 0.2e-3)  # np.arange(-10e-3, 10e-3, 0.2e-3)
    z_grid = np.arange(0e-3, 70e-3, 0.2e-3)    # np.arange(0e-3, 30e-3, 0.1e-3)

    tgc_sampling_points = np.linspace(np.min(z_grid), np.max(z_grid), 10)
    tgc_values = np.linspace(24, 34, 10)

    sequence_xy, sequence_yx = create_sequence(
        medium=medium,
        angles=angles,
        n_periods=n_periods,
        center_frequency=center_frequency,
        sample_range=sample_range,
        pri=pri
    )
    
    # times 2, because 2 TX/RXs must be performed per angle.
    expected_acquisition_time = 2 * (np.sum([op.pri for op in sequence_xy.ops]) + np.sum([op.pri for op in sequence_yx.ops]))
    expected_frame_rate = 1 / expected_acquisition_time


    print(f"Acquiring data, expected frame: {expected_frame_rate:.2f} Hz")
    capture_buffer, metadata = acquire(
        medium=medium,
        nframes=nframes,
        sequence_xy=sequence_xy,
        sequence_yx=sequence_yx,
        voltage=voltage,
        tgc_sampling_points=tgc_sampling_points,
        tgc_values=tgc_values,
        batch_size=batch_size,
    )

    rf = capture_buffer.get_data()
    
    print("Acquisition done.")
    time_intervals = get_time_intervals(
        data=rf,
        metadata=metadata
    )

    actual_frame_rate_per_batch = 1 / time_intervals

    print(f"Actual frame rate per batch (minimum, maxium): {np.min(actual_frame_rate_per_batch):.2f}, {np.max(actual_frame_rate_per_batch):.2f} Hz")


    filename = f"{name}_raw_dataset.pkl"
    print(f"Saving data to file: {filename}...")

    with open(filename, 'wb') as f:
        pickle.dump(
            {
                "expected_frame_rate": expected_frame_rate,
                "time_intervals": time_intervals,
                "rf": rf, 
                "metadata": metadata,
                "sequence_xy": sequence_xy,
                "sequence_yx": sequence_yx,
                "tgc_sampling_points": tgc_sampling_points,
                "tgc_values": tgc_values,
                "voltage": voltage
            },
            f
        )
    print("Done")
    



