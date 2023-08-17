import arrus
import arrus.medium
import arrus.ops.tgc
import arrus.session
import arrus.utils.imaging
import arrus.utils.us4r
import scipy.signal
from arrus.ops.us4r import Scheme
from arrus.utils.imaging import *

import probe_params
from reconstruction import get_pwi_reconstruction
from sequence import (
    create_sequence,
    get_system_sequence
)
from visualizer import VTKVisualizer


def main():
    with arrus.Session("us4r.prototxt") as sess:
        us4r = sess.get_device("/Us4R:0")
        us4r.set_hv_voltage(10)
        MEDIUM = arrus.medium.Medium(name="tissue", speed_of_sound=1540)
        angles = np.linspace(-10, 10, 64) * np.pi / 180  # [rad]
        center_frequency = 6e6  # [Hz]
        n_periods = 2
        sample_range = (0, 5 * 1024)
        pri = 400e-6

        # TX/RX PW sequence
        sequence_xy, sequence_yx = create_sequence(
            medium=MEDIUM,
            angles=angles,
            n_periods=n_periods,
            center_frequency=center_frequency,
            sample_range=sample_range,
            pri=pri
        )


        # Image reconstruction.
        fir_taps = scipy.signal.firwin(
            numtaps=64, cutoff=np.array([0.5, 1.5]) * center_frequency,
            pass_zero="bandpass", fs=us4r.current_sampling_frequency
        )

        pipeline = get_pwi_reconstruction(
            array_x=probe_params.APERTURE_X,
            array_y=probe_params.APERTURE_Y,
            y_grid=np.arange(-6e-3, 6e-3, 0.2e-3),
            x_grid=np.arange(-6e-3, 6e-3, 0.2e-3),
            z_grid=np.arange(25e-3, 43e-3, 0.2e-3),
            fir_taps=fir_taps,
            sequence_xy=sequence_xy,
            sequence_yx=sequence_yx,
            dr_min=-5, dr_max=120,
        )
        # TODO(pjarosik) avoid using the below wrapper
        pipeline_wrapper = Pipeline(
            steps=(
                Lambda(
                    lambda data: pipeline.process(data)[0],
                    prepare_func=lambda metadata: pipeline.prepare(metadata)[0]
                ),
            ),
            placement="/GPU:0"
        )
        scheme = Scheme(
            tx_rx_sequence=get_system_sequence(
                sequence_xy=sequence_xy,
                sequence_yx=sequence_yx,
                probe_model=us4r.get_probe_model(),
                device_sampling_frequency=us4r.current_sampling_frequency
            ),
            processing=pipeline_wrapper
        )
        buffer, output_metadata = sess.upload(scheme)
        visualizer = VTKVisualizer(output_metadata.input_shape, use_lgf=False)
        print("Press CTRL+C multiple times to stop the example")

        us4r.set_tgc([50] * 26)
        sess.start_scheme()
        while True:
            volume = buffer.get()[0]
            visualizer.update(volume)


if __name__ == "__main__":
    main()
