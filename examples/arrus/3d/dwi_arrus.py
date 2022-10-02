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
from datetime import datetime

arrus.set_clog_level(arrus.logging.INFO)
arrus.add_log_file("test.log", arrus.logging.INFO)


def main():
    # Here starts communication with the device.
    with arrus.Session("./us4r.prototxt") as sess:
        us4r = sess.get_device("/Us4R:0")
        us4r.set_hv_voltage(tx_voltage)
        probe_model = us4r.get_probe_model()

        tx_focus = [-20e-3]
        tx_ang_zx = [0]
        tx_ang_zy = [0]

        seq = get_sequence(
            probe_model,
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
        # Upload the scheme on the us4r-lite device.
        buffer, metadata = sess.upload(scheme)
        # Created 2D image display.
        display = Display2D(metadata=metadata, value_range=(-80, 0), cmap="gray",
                            title="B-mode", xlabel="OX+OY (px)",
                            ylabel="OZ (px)",
                            show_colorbar=True)
        # Start the scheme.
        sess.start_scheme()
        # Start the 2D display.
        # The below function blocks current thread until the window is closed.
        display.start(buffer)
        data = np.stack(rf_queue)
        current_date = datetime.today().strftime('%Y-%m-%d')
        filename = f"dwi_3d_{current_date}"
        pickle.dump({"rf": data, "metadata": metadata},
                    open(f"{filename}.pkl", "wb"))
        print("Display closed, stopping the script.")

    # When we exit the above scope, the session and scheme is properly closed.
    print("Stopping the example.")


if __name__ == "__main__":
    main()
