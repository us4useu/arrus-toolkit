import arrus
import arrus.session
import arrus.utils.imaging
import arrus.utils.us4r
from collections import deque
import numpy as np
import cupy as cp
import pathlib
import os.path
from arrus.utils.imaging import *

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
    SelectFrame,
    Squeeze,
    Lambda,
    RemapToLogicalOrder,
    SelectSequence
)
from arrus.utils.gui import (
    Display2D
)

arrus.set_clog_level(arrus.logging.INFO)
arrus.add_log_file("test.log", arrus.logging.INFO)

pitch = 0.2e-3  # [m]
n_elements_single_axis = 128
total_n_elements = 2*n_elements_single_axis
c = 1450  # [m/s]
center_frequency = 6e6
tx_voltage = 10
n_samples = 4096
angles = np.linspace(-10, 10, 16)*np.pi/180

rows = np.zeros(total_n_elements).astype(bool)
columns = np.zeros(total_n_elements).astype(bool)
columns[:n_elements_single_axis] = True
rows[n_elements_single_axis:] = True


def get_delays(angle):
    elements_z = np.zeros(n_elements_single_axis)
    elements_x = (np.arange(0, n_elements_single_axis)-n_elements_single_axis//2 + 0.5) * pitch
    delays = (elements_x * np.sin(angle) + elements_z * np.cos(angle)) / c  # [n_tx, n_elem]
    delays = delays - np.min(delays)
    return delays


def get_sequence(angles):
    ops = []
    # TX: rows, RX: columns
    tx_aperture = rows
    rx_aperture = columns
    for angle in angles:
        delays = get_delays(angle)
        op = TxRx(
            Tx(aperture=tx_aperture,
               excitation=Pulse(
                   center_frequency=center_frequency,
                   n_periods=2, inverse=False),
               delays=delays),
            Rx(aperture=rx_aperture,
               sample_range=(0, n_samples),
               downsampling_factor=1),
            pri=200e-6)
        ops.append(op)

    # TX: columns, RX: rows
    tx_aperture = columns
    rx_aperture = rows
    for angle in angles:
        delays = get_delays(angle)
        op = TxRx(
            Tx(aperture=tx_aperture,
               excitation=Pulse(
                   center_frequency=center_frequency,
                   n_periods=2, inverse=False),
               delays=delays),
            Rx(aperture=rx_aperture,
               sample_range=(0, n_samples),
               downsampling_factor=1),
            pri=200e-6)
        ops.append(op)

    return TxRxSequence(
        ops=ops,
        # Turn off TGC.
        tgc_curve=[],  # 34]*20,  # [dB]
        # Time between consecutive acquisitions, i.e. 1/frame rate.
        sri=15e-3
    )


CWD = os.path.dirname(os.path.abspath(__file__))


class ReconstructRCA(Operation):

    def __init__(self, output_grid, angles, speed_of_sound):
        self.x_grid, self.y_grid, self.z_grid = output_grid
        self.angles = angles
        self.speed_of_sound = speed_of_sound

    def prepare(self, const_metadata):
        kernel_source = pathlib.Path("delayAndSumLUT.cc").read_text()
        kernel_module = cp.RawModule(code=kernel_source)
        kernel_module.compile()
        self.kernel = kernel_module.get_function("delayAndSumLUT")
        # Note: we are assuming a probe with square (n+n) aperture.
        probe_model = const_metadata.context.device.probe.model
        fs = const_metadata.data_description.sampling_frequency
        fc = const_metadata.context.raw_sequence.ops[0].tx.excitation.center_frequency
        self.fs = np.float32(fs)
        self.fc = np.float32(fc)
        pitch = probe_model.pitch
        n_elements = n_elements_single_axis
        c = self.speed_of_sound
        n_batches, total_n_tx, self.n_rx, self.n_samples = const_metadata.input_shape
        assert n_batches == 1
        self.n_tx_one_axis = len(self.angles)
        self.n_x = len(self.x_grid)
        self.n_y = len(self.y_grid)
        self.n_z = len(self.z_grid)
        self.output_array = cp.zeros((self.n_tx_one_axis, self.n_y, self.n_x, self.n_z),
                                     dtype=cp.complex64)
        # Final output array
        self.final_output_array = cp.zeros((total_n_tx, self.n_y, self.n_x, self.n_z), dtype=cp.complex64)

        # Compute position of the each probe element. Note: the (0, 0, 0) is located in the center of probe.
        ri = cp.arange(n_elements)-n_elements//2+0.5
        ri = ri*pitch
        # Restructure all arrays that we will use later.
        x = cp.asarray(self.x_grid).reshape(1, -1, 1)      # (1,   nx, 1)
        y = cp.asarray(self.y_grid).reshape(1, -1, 1)      # (1,   ny, 1)
        z = cp.asarray(self.z_grid).reshape(1, 1, -1)      # (1,   1,  nz)
        gamma = cp.asarray(angles).reshape(-1, 1, 1)  # (ntx, 1,  1)
        ri = cp.asarray(ri).reshape(-1, 1, 1)         # (nrx, 1,  1)

        init_delays = []
        center_element = n_elements//2-1
        for op in const_metadata.context.raw_sequence.ops[:self.n_tx_one_axis]:
            init_del = (op.tx.delays[center_element] + op.tx.delays[center_element])/2
            init_delays.append(init_del)
        init_delays = cp.asarray(init_delays).reshape(-1, 1, 1)
        # Compute TX delays.
        tx_distance = z*np.cos(gamma) + x*cp.sin(gamma)  # [m]
        tx_delays = tx_distance/c  # [s]
        tx_delays += init_delays
        self.tx_delays = cp.asarray(tx_delays).astype(cp.float32)

        # Compute RX delays.
        rx_distance = cp.sqrt(z**2 + (y-ri)**2)
        rx_delays = rx_distance/c
        self.rx_delays = cp.asarray(rx_delays).astype(cp.float32)

        # Compute "TX apodization".
        d1 = -n_elements/2 * pitch
        d2 = n_elements/2 * pitch
        tx_apodization_left = z*cp.sin(gamma) + (d1-x)*cp.cos(gamma) <= 0
        tx_apodization_right = z*cp.sin(gamma) + (d2-x)*cp.cos(gamma) >= 0
        tx_apodization = np.logical_and(tx_apodization_left, tx_apodization_right)
        tx_apodization = tx_apodization.astype(np.uint8)
        self.tx_apodization = cp.asarray(tx_apodization).astype(np.uint8)

        # Compute RX apodization.
        max_rx_tang = 0.5  # Determined experimentally.
        rx_sigma = 1/2
        rx_tang = cp.abs((ri-y)/z)
        rx_apodization = cp.exp(-(rx_tang/max_rx_tang)**2 / (2*rx_sigma))
        rx_apodization[rx_tang > max_rx_tang] = 0.0
        self.rx_apodization = cp.ones_like(rx_apodization).astype(np.float32)
        return const_metadata.copy(input_shape=(
            total_n_tx, len(self.y_grid), len(self.x_grid), len(self.z_grid)))

    def single_run(self, data):
        params = (
            self.output_array,
            data,
            self.tx_apodization,
            self.rx_apodization,
            self.tx_delays,
            self.rx_delays,
            self.n_tx_one_axis, self.n_samples, self.n_rx,
            self.n_y, self.n_x, self.n_z,
            self.fs, self.fc
        )
        x_block_size = min(self.n_x, 8)
        y_block_size = min(self.n_y, 8)
        z_block_size = min(self.n_z, 8)
        block_size = (
            z_block_size,
            x_block_size,
            y_block_size
        )
        grid_size = (
            (self.n_z-1)//z_block_size+1,
            (self.n_x-1)//x_block_size+1,
            (self.n_y-1)//y_block_size+1
        )
        self.kernel(
            grid_size,
            block_size,
            params
        )
        return self.output_array

    def process(self, data):
        # Transmit x, receive with y
        data = cp.squeeze(data)
        xy_result = self.single_run(data[:self.n_tx_one_axis])
        xy_result = cp.ascontiguousarray(xy_result)
        self.final_output_array[:self.n_tx_one_axis] = xy_result
        # yx_result = self.single_run(data[self.n_tx_one_axis:])
        # yx_result = cp.ascontiguousarray(yx_result)
        # self.final_output_array[:self.n_tx_one_axis] = yx_result
        return self.final_output_array
