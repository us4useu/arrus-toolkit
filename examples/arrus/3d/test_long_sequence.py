"""
This script shows how users can run their own custom callback when new data
is acquired to the PC memory.

A callback measuring acquisition frame is used. The actual frame rate
can be controlled using PwiSequence sri parameter.
"""
import arrus
import arrus.session
import arrus.utils.imaging
import arrus.utils.us4r
import time
import sys
from parameters import *


arrus.set_clog_level(arrus.logging.TRACE)
arrus.add_log_file("test.log", arrus.logging.INFO)


class Timer:
    def __init__(self):
        self.last_time = time.time()

    def callback(self, element):
        now = time.time()
        dt = now-self.last_time
        print(f"Delta t: {dt:6.2f}, data size: {element.data.nbytes} bytes",
              end="\r")
        self.last_time = now
        element.release()


def main():
    with arrus.Session(sys.argv[1]) as sess:
        us4r = sess.get_device("/Us4R:0")
        probe_model = us4r.get_probe_model()
        seq = get_dwi_sequence(probe_model)

        scheme = Scheme(
            tx_rx_sequence=seq,
            rx_buffer_size=16,
            output_buffer=DataBufferSpec(type="FIFO", n_elements=16),
            work_mode="ASYNC")

        us4r.set_hv_voltage(5)
        time.sleep(1)
        us4r.disable_hv()
        time.sleep(1)
        input("Press enter to continue")
        # Upload sequence on the us4r-lite device.
        buffer, const_metadata = sess.upload(scheme)
        timer = Timer()
        buffer.append_on_new_data_callback(timer.callback)
        sess.start_scheme()
        work_time = 1*60  # [s]
        print(f"Running for {work_time} s")
        time.sleep(work_time)
    print("Finished.")


if __name__ == "__main__":
    main()
