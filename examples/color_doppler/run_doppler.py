import arrus
import arrus.session
import arrus.utils.imaging
import arrus.utils.us4r
import numpy as np
import scipy.signal
import cupyx.scipy.ndimage
import math
import matplotlib.pyplot as plt
import pickle
import arrus.metadata
import dataclasses
import numpy as np
from collections import deque
from arrus.utils.imaging import *
from datetime import datetime

from arrus.ops.us4r import (
    Scheme,
    Pulse,
    DataBufferSpec
)
from arrus.ops.imaging import (
    PwiSequence
)
from arrus.utils.imaging import (
    Pipeline,
    Transpose,
    BandpassFilter,
    FirFilter,
    Decimation,
    QuadratureDemodulation,
    EnvelopeDetection,
    LogCompression,
    Enqueue,
    ReconstructLri,
    Mean,
    Lambda,
    SelectFrames,
    Squeeze,
    RemapToLogicalOrder
)
from arrus.utils.gui import (
    Display2D,
    Layer2D
)
import keyboard

import cupy as cp


# Additional post-processing of Color Doppler data, to e.g. limit color doppler display
# only to place where high-velocity is detected.
def value_func(data):
    values = data[0]*1e3
    values[values < -200] = 200
    power_doppler = data[1]
    mask = np.abs(values) < 30
    mask = np.logical_or(mask, power_doppler < 80)
    values[mask] = None
    return values


arrus.set_clog_level(arrus.logging.INFO)
arrus.add_log_file("test.log", arrus.logging.INFO)


class ColorDoppler(arrus.utils.imaging.Operation):

    def __init__(self):
        pass

    def prepare(self, metadata):
        doppler_src = open("doppler.cc").read()
        self.doppler = cp.RawKernel(doppler_src, 'doppler')
        self.nframes, self.nx, self.nz = metadata.input_shape
        self.output_dtype = cp.float32
        self.output_shape = (2, self.nx, self.nz)  # color, power
        self.output = cp.zeros(self.output_shape, dtype=self.output_dtype)
        self.block = (32, 32)
        self.grid = math.ceil(self.nz/self.block[1]), math.ceil(self.nx/self.block[0])
        self.angle = set(metadata.context.sequence.angles.tolist())
        if len(self.angle) > 1:
            raise ValueError("Color doppler mode is implemented only for a "
                             "sequence with a single finite-value transmit "
                             "angle.")
        self.angle = next(iter(self.angle))
        self.pri = metadata.context.sequence.pri
        self.tx_frequency = metadata.context.sequence.pulse.center_frequency
        self.c = metadata.context.sequence.speed_of_sound
        self.scale = self.c/(2*np.pi*self.pri*self.tx_frequency*2*math.cos(self.angle))
        return metadata.copy(input_shape=self.output_shape, dtype=cp.float32, is_iq_data=False)

    def process(self, data):
        params = (
            self.output[0],  # Color
            self.output[1],  # Power
            data,
            self.nframes, self.nx, self.nz
        )
        self.doppler(self.grid, self.block, params)
        result = self.output
        result[0] = result[0]*self.scale    # [m/s]
        result[1] = 20*cp.log10(result[1])  # [dB]
        return result


class FilterWallClutter(arrus.utils.imaging.Operation):

    def __init__(self, w_n, n):
        self.w_n = w_n
        self.n = n

    def prepare(self, metadata):
        if self.n %2 == 0:
            self.actual_n = self.n+1
        self.taps = scipy.signal.firwin(self.actual_n, self.w_n, pass_zero=False)
        self.taps = cp.array(self.taps)
        return metadata

    def process(self, data):
        output= cupyx.scipy.ndimage.convolve1d(data, self.taps, axis=0)
        return output


def main():
    # github.com/us4useu/arrus

    # TX/RX sequence to upload on us4R-lite.
    angle = 10  # [deg]
    n_angles = 64
    center_frequency = 5e6  # [Hz]
    seq = PwiSequence(
        angles=np.array([angle*np.pi/180]),
        pulse=Pulse(center_frequency=center_frequency, n_periods=2, inverse=False),
        rx_sample_range=(0, 1024*2),
        speed_of_sound=1540,  # [m/s]
        pri=72e-6,
        tgc_start=54,
        tgc_slope=0,
        n_repeats=n_angles
    )

    # Raw channel data processing to do on GPU device.
    x_grid = np.arange(-15, 15, 0.13) * 1e-3
    z_grid = np.arange(0, 20, 0.13) * 1e-3

    taps = scipy.signal.firwin(64, np.array([0.5, 1.5])*center_frequency, pass_zero=False, fs=65e6)

    queue = deque(maxlen=100)
    scheme = Scheme(
        tx_rx_sequence=seq,
        processing=Pipeline(
            steps=(
                RemapToLogicalOrder(),
                Lambda(lambda data: (queue.append(data.get()), data)[1]),
                Transpose(axes=(0, 1, 3, 2)),
                # BandpassFilter(),
                FirFilter(taps),
                QuadratureDemodulation(),
                Decimation(decimation_factor=4, cic_order=2),
                ReconstructLri(x_grid=x_grid, z_grid=z_grid),
                Squeeze(),
                Pipeline(
                    steps=(
                        FilterWallClutter(w_n=0.3, n=64),
                        ColorDoppler(),
                        Transpose(axes=(0, 2, 1)),
                    ),
                    placement="/GPU:0"
                ),
                SelectFrames([0]),
                Squeeze(),
                EnvelopeDetection(),
                Transpose(),
                Lambda(lambda data: data/cp.nanmax(data)),
                LogCompression(),
            ),
            placement="/GPU:0"
        )
    )

    # Here starts communication with the device.
    with arrus.Session("/home/pjarosik/us4r.prototxt") as sess:
        us4r = sess.get_device("/Us4R:0")
        us4r.set_hv_voltage(30)

        # Upload sequence on the us4r-lite device.
        buffer, (doppler_metadata, bmode_metadata) = sess.upload(scheme)
        # buffer, metadata = sess.upload(scheme)

        # display = Display2D(metadata=metadata, value_range=(20, 80), cmap="gray",
        #                 title="B-mode", xlabel="OX (mm)", ylabel="OZ (mm)",
        #                 extent=get_extent(x_grid, z_grid)*1e3,
        #                 show_colorbar=True)

        display = Display2D(
            layers=(
                Layer2D(metadata=bmode_metadata, value_range=(-40, 0), cmap="gray", input=0),
                Layer2D(metadata=doppler_metadata, cmap="bwr", value_range=(-300, 300), input=1, value_func=value_func)
            )
            # xlabel="OX (mm)", ylabel="OZ (mm)",
            # extent=arrus.utils.imaging.get_extent(x_grid*1e3, z_grid*1e3),
            # show_colorbar=1, colorbar_fraction=0.025, colorbar_title="Velocity (mm/s)",
        )
        sess.start_scheme()
        display.start(buffer)

        print("Display closed, stopping the script.")
    pickle.dump({"rf": np.stack(queue), "metadata": bmode_metadata}, open(f"data_doppler_{datetime.now().strftime('%H-%M-%S')}.pkl", "wb"))
    print("Stopping the example.")


if __name__ == "__main__":
    main()
