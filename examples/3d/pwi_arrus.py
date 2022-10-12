import arrus
import arrus.session
import arrus.utils.imaging
import arrus.utils.us4r
import pickle
from datetime import datetime

from arrus.utils.imaging import *
from arrus.utils.gui import Display2D
from parameters import *

arrus.set_clog_level(arrus.logging.INFO)
arrus.add_log_file("test.log", arrus.logging.INFO)


def initialize():
    sess = arrus.Session("us4r.prototxt")
    us4r = sess.get_device("/Us4R:0")
    us4r.set_hv_voltage(tx_voltage)
    probe_model = us4r.get_probe_model()

    tx_focus = [np.inf]
    tx_ang_zx = [0]
    tx_ang_zy = [0]

    seq = get_sequence(
        probe_model=probe_model,
        tx_focus=tx_focus, tx_ang_zx=tx_ang_zx, tx_ang_zy=tx_ang_zy)

    processing, rf_queue = get_imaging(
        sequence=seq,
        tx_focus=tx_focus, tx_ang_zx=tx_ang_zx, tx_ang_zy=tx_ang_zy)
    # Declare the complete scheme to execute on the devices.
    scheme = Scheme(
        tx_rx_sequence=seq,
        processing=processing
    )
    us4r.set_tgc((tgc_t, tgc_values))
    buffer, metadata = sess.upload(scheme)
    return buffer, metadata


def main():
    # Here starts communication with the device.

        display = Display2D(metadata=metadata,
                            value_range=(-80, 0), cmap="gray",
                            title="B-mode", xlabel="OX (mm)", ylabel="OZ (mm)",
                            show_colorbar=True)
        # Start the scheme.
        input("Press any key to continue")
        sess.start_scheme()
        # Start the 2D display.
        display.start(buffer)
        data = np.stack(rf_queue)
        current_date = datetime.today().strftime('%Y-%m-%d')
        # The below function blocks current thread until the window is closed.
        filename = f"pwi_3d_{current_date}"
        pickle.dump({"rf": data, "metadata": metadata},
                    open(f"{filename}.pkl", "wb"))
        print("Display closed, stopping the script.")

    # When we exit the above scope, the session and scheme is properly closed.
    print("Stopping the example.")


if __name__ == "__main__":
    main()
