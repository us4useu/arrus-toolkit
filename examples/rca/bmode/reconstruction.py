from arrus.utils.imaging import *
import cupy as cp
import numpy as np
from arrus.ops.us4r import TxRxSequence
import dataclasses
import arrus.metadata
import arrus


class Concatenate(Operation):
    def __init__(self, axis):
        super().__init__()
        self.axis = axis

    def prepare(self, const_metadata):
        # TODO verify that all the metadata objects are compatible, i.e. have the same data type, etc.
        axis_total_size = 0
        for cm in const_metadata:
            axis_total_size += cm.input_shape[self.axis]
        output_shape = list(const_metadata[0].input_shape)
        output_shape[self.axis] = axis_total_size
        return const_metadata[0].copy(input_shape=tuple(output_shape))

    def process(self, data):
        return cp.concatenate(data, axis=self.axis)


class Slice(Operation):

    def __init__(self, axis, position=None):
        super().__init__()
        self.axis = axis
        self.position = position

    def prepare(self, const_metadata):
        input_shape = list(const_metadata.input_shape)

        if self.position is None:
            self.position = input_shape[self.axis]//2

        self.slicing = [slice(None)] * len(input_shape)
        self.slicing[self.axis] = self.position
        self.slicing = tuple(self.slicing)

        # Updating metadata.
        input_shape.pop(self.axis)
        output_shape = tuple(input_shape)
        output_description = const_metadata.data_description
        if output_description.spacing is not None:
            new_coordinates = list(output_description.spacing.coordinates)
            new_coordinates.pop(self.axis)
            new_coordinates = tuple(new_coordinates)
            output_spacing = dataclasses.replace(output_description.spacing, coordinates=new_coordinates)
            output_description = dataclasses.replace(
                const_metadata.data_description,
                spacing=output_spacing
            )

        return const_metadata.copy(
            input_shape=output_shape,
            data_desc=output_description
        )

    def process(self, data):
        return data[self.slicing]


class ReconstructHriRca(Operation):
    def __init__(
            self,
            y_grid: np.ndarray,
            x_grid: np.ndarray,
            z_grid: np.ndarray,
            tx_orientation: str,
            rx_orientation: str,
            min_tang=-0.5, max_tang=0.5, name=None
    ):
        """
        RCA beamformer.

        The beamformer reconstructs High-resolution Image (i.e.
        compounds LRIs).

        :param tx_orientation: the orientation of TX probe, one of: "ox", "oy"
        :param rx_orientation: the orientation of RX probe, one of: "ox", "oy"
        """
        super().__init__(name)
        self.y_grid = y_grid
        self.x_grid = x_grid
        self.z_grid = z_grid
        self.array_rx_orientation = rx_orientation
        self.array_tx_orientation = tx_orientation
        if min_tang > max_tang:
            raise ValueError(f"min tang {min_tang} "
                             f"should be <= max tang {max_tang}")
        self.min_tang = min_tang
        self.max_tang = max_tang
        self.n_sigma = 3.0
        self.num_pkg = cp

    def set_pkgs(self, num_pkg, **kwargs):
        if num_pkg is np:
            raise ValueError(
                f"{ReconstructHriRca.__name__} is implemented for GPU only.")

    def set_parameter(self, key: str, value: Sequence[Number]):
        if not hasattr(self, key):
            raise ValueError(f"{type(self).__name__} has no {key} parameter.")
        # TODO assuming scalar parameters only
        setattr(self, key, cp.float32(value))

    def get_parameter(self, key: str) -> Sequence[Number]:
        if not hasattr(self, key):
            raise ValueError(f"{type(self).__name__} has no {key} parameter.")
        return getattr(self, key)

    def get_parameters(self) -> Dict[str, ParameterDef]:
        return {
            "min_tang": ParameterDef(name="min_tang",
                space=Box(
                    shape=(1,),
                    dtype=np.float32,
                    low=-np.inf,
                    high=np.inf
                ),
            ),
            "max_tang": ParameterDef(name="max_tang",
                space=Box(
                    shape=(1,),
                    dtype=np.float32,
                    low=-np.inf,
                    high=np.inf
                ),
            ),
        }

    def prepare(self, const_metadata):
        current_dir = os.path.dirname(os.path.join(os.path.abspath(__file__)))
        kernel_path = Path(current_dir) / "reconstruct.cu"
        kernel_source = kernel_path.read_text()
        self.kernel_module = cp.RawModule(code=kernel_source)
        self.kernel = self.kernel_module.get_function("iqRaw2Hri")
        # INPUT PARAMETERS.
        # Input data shape.
        self.n_seq, self.n_tx, self.n_rx, self.n_samples = const_metadata.input_shape
        if self.n_seq > 1:
            raise ValueError("At most 1 TX/RX sequence is supported by this beamformer")

        self.sequence = const_metadata.context.sequence
        self.raw_sequence = const_metadata.context.raw_sequence
        reference_op = self.sequence.ops[0]
        array_tx_id = self.sequence.get_tx_probe_id_unique()
        array_rx_id = self.sequence.get_rx_probe_id_unique()

        self.array_tx = const_metadata.context.device.get_probe_by_id(array_tx_id).model
        self.array_rx = const_metadata.context.device.get_probe_by_id(array_rx_id).model

        start_sample = reference_op.rx.sample_range[0]
        speed_of_sound = reference_op.tx.speed_of_sound
        pulse = reference_op.tx.excitation

        self.y_size = len(self.y_grid)
        self.x_size = len(self.x_grid)
        self.z_size = len(self.z_grid)

        output_shape = (1, self.y_size, self.x_size, self.z_size)

        self.output = cp.zeros(
            output_shape, dtype=cp.complex64
        )
        y_block_size = min(self.y_size, 8)
        x_block_size = min(self.x_size, 8)
        z_block_size = min(self.z_size, 8)

        self.block_size = (z_block_size, x_block_size, y_block_size)
        self.grid_size = (int((self.z_size - 1) // z_block_size + 1),
                          int((self.x_size - 1) // x_block_size + 1),
                          int((self.y_size - 1) // y_block_size + 1))

        self.y_pix = cp.asarray(self.y_grid, dtype=cp.float32)
        self.x_pix = cp.asarray(self.x_grid, dtype=cp.float32)
        self.z_pix = cp.asarray(self.z_grid, dtype=cp.float32)

        # System and transmit properties.
        self.sos = cp.float32(speed_of_sound)
        self.fs = cp.float32(const_metadata.data_description.sampling_frequency)
        self.fn = cp.float32(pulse.center_frequency)

        self.probe_tx_pitch = cp.float32(self.array_tx.pitch)
        self.probe_tx_n_elements = cp.int32(self.array_tx.n_elements)
        tx_orientation = self.get_arrangement_code(self.array_tx_orientation)
        self.array_tx_orientation = cp.uint8(tx_orientation)

        self.probe_rx_pitch = cp.float32(self.array_rx.pitch)
        self.probe_rx_n_elements = cp.int32(self.array_rx.n_elements)
        rx_arrangement = self.get_arrangement_code(self.array_rx)
        self.array_rx_orientation = cp.uint8(rx_arrangement)

        # TX focus and angle
        tx_focus = (op.tx.focus for op in self.sequence.ops)
        # TODO: this assumption, i.e. foc None means inf should be moved to ARRUS imaging
        tx_focus = [foc if foc is not None else cp.inf for foc in tx_focus]
        self.tx_focus = cp.asarray(tx_focus, dtype=cp.float32)
        # TODO: this assumption, i.e. foc None means inf should be moved to ARRUS imaging
        tx_angle = (op.tx.angle for op in self.sequence.ops)
        tx_angle = [angle if angle is not None else 0.0 for angle in tx_angle]
        self.tx_angle = cp.asarray(tx_angle, dtype=cp.float32)

        # TX aperture centers.
        tx_ap_cent = [self.get_ap_center(op.tx.aperture, self.array_tx)
                      for op in self.sequence.ops]
        self.tx_ap_cent = cp.asarray(tx_ap_cent, dtype=cp.float32)

        # first/last probe element in TX aperture
        tx_ap_begin, tx_ap_end = self.get_tx_ap_bounds(self.raw_sequence)
        self.tx_ap_begin = cp.asarray(tx_ap_begin, dtype=cp.float32)
        self.tx_ap_end = cp.asarray(tx_ap_end, dtype=cp.float32)

        rx_ap_origin = self.get_rx_ap_origin(self.raw_sequence)
        self.rx_ap_origin = cp.asarray(rx_ap_origin, dtype=cp.int32)

        # Min/max tang
        self.min_tang = cp.float32(self.min_tang)
        self.max_tang = cp.float32(self.max_tang)
        burst_factor = pulse.n_periods / (2 * self.fn)
        tx_cent_delay = arrus.kernels.tx_rx_sequence.get_center_delay(
            sequence=self.sequence,
            probe_tx=self.array_tx,
            probe_rx=self.array_rx,
        )
        self.init_delay = -start_sample / const_metadata.context.device.sampling_frequency
        self.init_delay = burst_factor + tx_cent_delay
        self.init_delay = cp.float32(self.init_delay)
        # Output metadata
        new_signal_description = dataclasses.replace(
            const_metadata.data_description,
            spacing=arrus.metadata.Grid(
                coordinates=(self.y_grid, self.x_grid, self.z_grid)
            )
        )
        return const_metadata.copy(
            input_shape=output_shape,
            data_desc=new_signal_description
        )

    def process(self, data):
        data = cp.ascontiguousarray(data)

        params = (
            self.output,
            data,
            self.n_tx, self.n_samples, self.n_rx,
            self.z_pix, self.z_size,
            self.x_pix, self.x_size,
            self.y_pix, self.y_size,
            self.sos, self.fs, self.fn,
            self.tx_focus, self.tx_angle,
            self.tx_ap_cent,
            self.probe_tx_pitch, self.probe_tx_n_elements,
            self.array_tx_orientation,
            self.probe_rx_pitch, self.probe_rx_n_elements,
            self.array_rx_orientation,
            self.tx_ap_begin, self.tx_ap_end,
            self.rx_ap_origin,
            self.min_tang, self.max_tang,
            self.init_delay,
            self.n_sigma
        )
        self.kernel(self.grid_size, self.block_size, params)
        return self.output

    def get_arrangement_code(self, orientation: str):
        return 0 if orientation == "ox" else 1

    def get_tx_ap_bounds(self, raw_sequence: TxRxSequence):
        first_els = []
        last_els = []
        for op in raw_sequence.ops:
            ap_elems = np.argwhere(op.tx.aperture)
            first, last = np.min(ap_elems), np.max(ap_elems)
            first_els.append(first)
            last_els.append(last)
        first_els, last_els = np.asarray(first_els), np.asarray(last_els)
        probe_model = self.array_tx
        pos_x = probe_model.element_pos_x
        return pos_x[first_els], pos_x[last_els]

    def get_rx_ap_origin(self, raw_sequence: TxRxSequence):
        origins = []
        for op in raw_sequence.ops:
            ap_elems = np.argwhere(op.rx.aperture)
            first = np.min(ap_elems)
            origins.append(first)
        return np.asarray(origins)

    def get_ap_center(self, aperture: arrus.ops.us4r.Aperture, probe):
        """
        Returns the physical location [m] of the aperture center for the
        given probe.

        :param probe: probe model description (arrus.devices.probe.ProbeModel)
        """
        if aperture.center is None and aperture.center_element is None:
            return 0.0  # default - center of the probe
        if aperture.center is not None:
            return aperture.center
        elif aperture.center_element is not None:
            n_elem = probe.n_elements
            element_pos = (np.arange(0, n_elem)-(n_elem-1)/2)*probe.pitch
            return np.interp(
                aperture.center_element,
                np.arange(0, n_elem),
                element_pos
            )
