import sys
import arrus
import arrus.session
import arrus.utils.imaging
import arrus.utils.us4r
import pickle
from parameters import *
from arrus.ops.us4r import *
from arrus.utils.imaging import *
from arrus.utils.gui import Display2D

arrus.set_clog_level(arrus.logging.INFO)
arrus.add_log_file("test.log", arrus.logging.INFO)


def main():
    # Here starts communication with the device.
    with arrus.Session("./us4r.prototxt") as sess:
        us4r = sess.get_device("/Us4R:0")
        us4r.set_hv_voltage(5)
        
        probe_model = us4r.get_probe_model()
        seq = get_dwi_sequence(probe_model)
        processing, rf_queue = get_dwi_imaging(sequence=seq)
        # Declare the complete scheme to execute on the devices.
        scheme = Scheme(
            tx_rx_sequence=seq,
            processing=processing
        )
        us4r.set_tgc(np.linspace(0, 60, 10), np.linspace(14, 54, 10))
        # Upload the scheme on the us4r-lite device.
        buffer, metadata = sess.upload(scheme)
        # Created 2D image display.
        display = Display2D(metadata=metadata, value_range=(20, 80), cmap="gray",
                            title="B-mode", xlabel="OX+Y (mm)", ylabel="OZ (mm)",
                            show_colorbar=True)
        # Start the scheme.
        sess.start_scheme()
        # Start the 2D display.
        # The below function blocks current thread until the window is closed.
        display.start(buffer)
        data = np.stack(rf_queue)
        filename = f"dwi_3d_{sys.argv[1]}"
        pickle.dump({"rf": data, "metadata": metadata},
                    open(f"{filename}.pkl", "wb"))
        print("Display closed, stopping the script.")

    # When we exit the above scope, the session and scheme is properly closed.
    print("Stopping the example.")


if __name__ == "__main__":
    main()
