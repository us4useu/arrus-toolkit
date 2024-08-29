import cupy as cp
import scipy.signal
import numpy as np

import arrus.medium

import probe_params
from dataset import load_rca_cyst_dataset
from sequence import create_sequence
from reconstruction import get_pwi_reconstruction

from visualizer import VTKVisualizer


def main():
    rf, metadata = load_rca_cyst_dataset()
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
        pass_zero="bandpass", fs=metadata.context.device.sampling_frequency
    )

    pipeline = get_pwi_reconstruction(
        array_x=probe_params.APERTURE_X,
        array_y=probe_params.APERTURE_Y,
        y_grid=np.arange(-6e-3, 6e-3, 0.4e-3),
        x_grid=np.arange(-6e-3, 6e-3, 0.4e-3),
        z_grid=np.arange(25e-3, 43e-3, 0.4e-3),
        fir_taps=fir_taps,
        sequence_xy=sequence_xy,
        sequence_yx=sequence_yx,
        volume_dr_min=-5, volume_dr_max=120,
    )
    output_metadata = pipeline.prepare(metadata)
    visualizer = VTKVisualizer(output_metadata[0].input_shape, use_lgf=False)

    print("Press CTRL+C multiple times to stop the example")

    n_frames = len(rf)

    i = 0
    while True:
        output = pipeline.process(cp.asarray(rf[i % n_frames]))
        vol = output[0].get()
        visualizer.update(vol)
        i += 1


if __name__ == "__main__":
    main()
