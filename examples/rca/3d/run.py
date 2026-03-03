import arrus
import arrus.medium
import arrus.ops.tgc
import arrus.session
import arrus.utils.imaging
import arrus.utils.us4r
import scipy.signal
from arrus.ops.us4r import Scheme
from arrus.ops.tgc import LinearTgc
from arrus.ops.tgc import LinearTgc
from arrus.ops.tgc import LinearTgc
from arrus.ops.tgc import LinearTgc
from arrus.utils.imaging import *
import numpy as np

from visualizer import VTKVisualizer

from arrus_rca_utils.reconstruction import (
    get_reconstruction_graph
)
from arrus_rca_utils.sequence import (
    get_pw_sequence
)

def main():

    medium = arrus.medium.Medium(name="ats560h", speed_of_sound=1450)
    with arrus.Session("us4r.prototxt", medium) as sess:
        us4r = sess.get_device("/Us4R:0")
        us4r.set_hv_voltage(30)

        ox_ordinal = 0
        oy_ordinal = 1
        array_ox = us4r.get_probe_model(ordinal=ox_ordinal)
        array_oy = us4r.get_probe_model(ordinal=oy_ordinal)
        array_ox_id = f"Probe:{ox_ordinal}"
        array_oy_id = f"Probe:{oy_ordinal}"

        n_samples = 5 * 1024

        angles = np.linspace(-10, 10, 32) * np.pi / 180  # [rad]
        center_frequency = 6e6  # [Hz]
        n_periods = 2
        sample_range = (0, n_samples)
        pri = 400e-6

        # Imaging grid.
        y_grid = np.arange(-8e-3, 8e-3, 0.4e-3)
        x_grid = np.arange(-8e-3, 8e-3, 0.4e-3)
        z_grid = np.arange(20e-3, 40e-3, 0.4e-3)

        common_parameters = dict(
            medium=medium,
            angles=angles,
            n_periods=n_periods,
            center_frequency=center_frequency,
            sample_range=sample_range,
            pri=pri,
        )

        # TX with OX elements, RX with OY elements
        sequence_xy = get_pw_sequence(
            array_tx_id=array_ox_id,
            array_tx=array_ox,
            array_rx_id=array_oy_id,
            array_rx=array_oy,
            name="XY",
            **common_parameters
        )
        # TX with OY elements, RX with OX elements
        sequence_yx = get_pw_sequence(
            array_tx_id=array_oy_id,
            array_tx=array_oy,
            array_rx_id=array_ox_id,
            array_rx=array_ox,
            name="YX",
            **common_parameters
        )

        fir_taps = scipy.signal.firwin(
            numtaps=64, cutoff=np.array([0.5, 1.5]) * center_frequency,
            pass_zero="bandpass", fs=us4r.current_sampling_frequency
        )

        reconstruction = get_reconstruction_graph(
            fir_taps=fir_taps,
            x_grid=x_grid, y_grid=y_grid, z_grid=z_grid,
            sequence_xy=sequence_xy,
            sequence_yx=sequence_yx
        )
        scheme = Scheme(
            tx_rx_sequence=[
                sequence_xy,
                sequence_yx
            ],
            processing=reconstruction
        )
        buffer, output_metadata = sess.upload(scheme)
        visualizer = VTKVisualizer(output_metadata.input_shape, use_lgf=False)
        print("Press CTRL+C multiple times to stop the example")

        us4r.set_tgc(LinearTgc(start=50, slope=0))
        sess.start_scheme()
        while True:
            volume = buffer.get()[0]
            visualizer.update(volume)


if __name__ == "__main__":
    main()
