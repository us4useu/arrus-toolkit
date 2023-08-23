from arrus_rca_utils.sequence import (
    _get_system_sequence_alternate_arrangement,
    _get_system_sequence_same_arrangement
)
import copy
from arrus.utils.imaging import *
import itertools
import cupy as cp
from arrus.ops.us4r import TxRxSequence
import arrus_rca_utils.probe_params as probe_params
import dataclasses
import arrus.metadata


def get_frame_ranges(*seqs):
    lengths = [len(s.ops) for s in seqs]
    r = list(itertools.accumulate(lengths))
    l = [0] + r[:-1]
    return zip(l, r)


def get_rx_aperture_size(*seqs):
    return tuple(s.ops[0].rx.aperture.size for s in seqs)


def _get_param_name(op_name: str, param_name: str):
    param_name = param_name.strip()
    if not param_name.startswith("/"):
        param_name = f"/{param_name}"
    return f"/{op_name}{param_name}"


class GetFramesForRange(Operation):

    def __init__(self, frames, aperture_size):
        super().__init__()
        self.frame_numbers = frames
        self.aperture_size = aperture_size

    def prepare(self, const_metadata):
        # new FCM
        old_fcm = const_metadata.data_description.custom["frame_channel_mapping"]
        frame_slice = slice(*self.frame_numbers)
        frames = old_fcm.frames[frame_slice, :self.aperture_size]
        channels = old_fcm.channels[frame_slice, :self.aperture_size]
        us4oems = old_fcm.us4oems[frame_slice, :self.aperture_size]
        new_fcm = dataclasses.replace(old_fcm, frames=frames,
                                      channels=channels, us4oems=us4oems)
        new_custom_fields = copy.deepcopy(const_metadata.data_description.custom)
        new_custom_fields["frame_channel_mapping"] = new_fcm
        new_data_desc = dataclasses.replace(
            const_metadata.data_description, custom=new_custom_fields)
        return const_metadata.copy(data_desc=new_data_desc)

    def process(self, data):
        return data


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


class SelectBatch(Operation):

    def __init__(self, indices):
        super().__init__()
        if not isinstance(indices, Iterable):
            indices = [indices]
        self.indices = indices

    def prepare(self, const_metadata):
        result = [const_metadata[i] for i in self.indices]
        if len(result) == 1:
            return result[0]
        else:
            return result

    def process(self, data):
        result = [data[i] for i in self.indices]
        if len(result) == 1:
            return result[0]
        return result


class Slice(Operation):

    def __init__(self, axis, position=None):
        super().__init__()
        self.axis = axis
        self.position = position

    def prepare(self, const_metadata):
        input_shape = list(const_metadata.input_shape)

        if self.position is None:
            self.position = input_shape[self.axis]//2

        self.slicing = [None] * len(input_shape)
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


class PipelineSequence(Pipeline):

    def __init__(self, pipelines):
        # Intentionally not calling super constructor.
        self.pipelines = pipelines
        self._set_names()
        self._determine_params()

    def prepare(self, metadata):
        m = metadata
        for p in self.pipelines:
            m = p.prepare(m)
        return m

    def process(self, data):
        d = data
        for p in self.pipelines:
            d = p.process(d)
        return d

    def __call__(self, data):
        return self.process(data)

    def set_parameter(self, key: str, value: Sequence[Number]):
        pipeline, pipeline_param_name = self._pipeline_params[key]
        pipeline.set_parameter(pipeline_param_name, value)

    def get_parameter(self, key: str) -> Sequence[Number]:
        pipeline, pipeline_param_name = self._pipeline_params[key]
        return pipeline.get_parameter(pipeline_param_name)

    def get_parameters(self) -> Dict[str, ParameterDef]:
        return self._param_defs

    def _set_names(self):
        for i, pipeline in enumerate(self.pipelines):
            if not hasattr(pipeline, "name") or pipeline.name is None:
                pipeline.name = f"Pipeline:{i}"

    def _determine_params(self):
        self._pipeline_params = {}
        self._param_defs = {}
        for pipeline in self.pipelines:
            name = pipeline.name
            params = pipeline.get_parameters()
            for k, param_def in params.items():
                prefixed_k = _get_param_name(name, k)
                self._pipeline_params[prefixed_k] = pipeline, k
                self._param_defs[prefixed_k] = param_def


class ReconstructHriRca(Operation):
    def __init__(
            self, y_grid, x_grid, z_grid,
            probe_tx: probe_params.ProbeArray,
            probe_rx: probe_params.ProbeArray,
            sequence, arrangement="alternate",
            min_tang=-0.5, max_tang=0.5, name=None
    ):
        super().__init__(name)
        self.y_grid = y_grid
        self.x_grid = x_grid
        self.z_grid = z_grid
        self.sequence = sequence
        self.array_rx = probe_rx
        self.array_tx = probe_tx
        if min_tang > max_tang:
            raise ValueError(
                f"min tang {min_tang} should be <= max tang {max_tang}")
        self.min_tang = min_tang
        self.max_tang = max_tang
        self.arrangement = arrangement
        self.n_sigma = 3.0
        self.num_pkg = cp

    def set_pkgs(self, num_pkg, **kwargs):
        if num_pkg is np:
            raise ValueError(
                f"{ReconstructHriRca.__name__} is implemented for GPU only.")

    def set_parameter(self, key: str, value: Sequence[Number]):
        if not hasattr(self, key):
            raise ValueError(f"{type(self).__name__} has no {key} parameter.")
        # TODO assumming scalar parameters only
        setattr(self, key, self.num_pkg.float32(value))

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
        self.kernel_module = self.num_pkg.RawModule(code=kernel_source)
        self.kernel = self.kernel_module.get_function("iqRaw2Hri")

        # INPUT PARAMETERS.
        # Input data shape.
        self.n_seq, self.n_tx, self.n_rx, self.n_samples = const_metadata.input_shape
        if self.n_seq > 1:
            raise ValueError("At most 1 sequence is supported.")

        if self.arrangement == "alternate":
            raw_seq, tx_cent_delay = _get_system_sequence_alternate_arrangement(
                sequence=self.sequence,
                array_tx=self.array_tx, array_rx=self.array_rx,
                device_sampling_frequency=const_metadata.context.device.sampling_frequency
            )
        elif self.arrangement in {"same_x", "same_y"}:
            raw_seq, tx_cent_delay = _get_system_sequence_same_arrangement(
                sequence=self.sequence,
                array=self.array_tx,
                arrangement=self.arrangement,
                metadata=const_metadata
            )
        else:
            raise ValueError(f"Unknown aperture orientation: {self.arrangement}")

        reference_op = self.sequence.ops[0]
        start_sample = reference_op.rx.sample_range[0]
        speed_of_sound = reference_op.tx.speed_of_sound
        pulse = reference_op.tx.excitation

        self.y_size = len(self.y_grid)
        self.x_size = len(self.x_grid)
        self.z_size = len(self.z_grid)

        output_shape = (1, self.y_size, self.x_size, self.z_size)

        self.output_buffer = self.num_pkg.zeros(
            output_shape, dtype=self.num_pkg.complex64
        )
        y_block_size = min(self.y_size, 8)
        x_block_size = min(self.x_size, 8)
        z_block_size = min(self.z_size, 8)

        self.block_size = (z_block_size, x_block_size, y_block_size)
        self.grid_size = (int((self.z_size - 1) // z_block_size + 1),
                          int((self.x_size - 1) // x_block_size + 1),
                          int((self.y_size - 1) // y_block_size + 1))

        self.y_pix = self.num_pkg.asarray(
            self.y_grid, dtype=self.num_pkg.float32)
        self.x_pix = self.num_pkg.asarray(
            self.x_grid, dtype=self.num_pkg.float32)
        self.z_pix = self.num_pkg.asarray(
            self.z_grid, dtype=self.num_pkg.float32)

        # System and transmit properties.
        self.sos = self.num_pkg.float32(speed_of_sound)
        self.fs = self.num_pkg.float32(
            const_metadata.data_description.sampling_frequency)
        self.fn = self.num_pkg.float32(pulse.center_frequency)

        self.probe_tx_pitch = self.num_pkg.float32(self.array_tx.pitch)
        self.probe_tx_n_elements = self.num_pkg.int32(self.array_tx.n_elements)
        tx_arrangement = self.get_arrangement_code(self.array_tx)
        self.array_tx_arrangement = self.num_pkg.uint8(tx_arrangement)

        self.probe_rx_pitch = self.num_pkg.float32(self.array_rx.pitch)
        self.probe_rx_n_elements = self.num_pkg.int32(self.array_rx.n_elements)
        rx_arrangement = self.get_arrangement_code(self.array_rx)
        self.array_rx_arrangement = self.num_pkg.uint8(rx_arrangement)

        # TX focus and angle
        tx_focus = (op.tx.focus for op in self.sequence.ops)
        tx_focus = [foc if foc is not None else np.inf for foc in tx_focus]
        self.tx_focus = self.num_pkg.asarray(
            tx_focus,
            dtype=self.num_pkg.float32
        )

        tx_angle = (op.tx.angle for op in self.sequence.ops)
        tx_angle = [angle if angle is not None else 0.0 for angle in tx_angle]
        self.tx_angle = self.num_pkg.asarray(
            tx_angle,
            dtype=self.num_pkg.float32
        )

        # TX aperture centers.
        tx_ap_cent = [
            self.get_ap_center(op.tx.aperture, self.array_tx)
            for op in self.sequence.ops
        ]
        self.tx_ap_cent = self.num_pkg.asarray(
            tx_ap_cent,
            dtype=self.num_pkg.float32
        )
        # first/last probe element in TX aperture
        tx_ap_begin, tx_ap_end = self.get_tx_ap_bounds(raw_seq)
        self.tx_ap_begin = self.num_pkg.asarray(
            tx_ap_begin,
            dtype=self.num_pkg.float32
        )
        self.tx_ap_end = self.num_pkg.asarray(
            tx_ap_end,
            dtype=self.num_pkg.float32
        )
        rx_ap_origin = self.get_rx_ap_origin(raw_seq)
        self.rx_ap_origin = self.num_pkg.asarray(
            rx_ap_origin,
            dtype=self.num_pkg.int32)

        # Min/max tang
        self.min_tang = self.num_pkg.float32(self.min_tang)
        self.max_tang = self.num_pkg.float32(self.max_tang)
        burst_factor = pulse.n_periods / (2 * self.fn)
        self.initial_delay = -start_sample/const_metadata.context.device.sampling_frequency
        self.initial_delay = burst_factor + tx_cent_delay
        self.initial_delay = self.num_pkg.float32(self.initial_delay)
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
        data = self.num_pkg.ascontiguousarray(data)
        params = (
            self.output_buffer,
            data,
            self.n_tx, self.n_samples, self.n_rx,
            self.z_pix, self.z_size,
            self.x_pix, self.x_size,
            self.y_pix, self.y_size,
            self.sos, self.fs, self.fn,
            self.tx_focus, self.tx_angle,
            self.tx_ap_cent,
            self.probe_tx_pitch, self.probe_tx_n_elements,
            self.array_tx_arrangement,
            self.probe_rx_pitch, self.probe_rx_n_elements,
            self.array_rx_arrangement,
            self.tx_ap_begin, self.tx_ap_end,
            self.rx_ap_origin,
            self.min_tang, self.max_tang,
            self.initial_delay,
            self.n_sigma
        )
        self.kernel(self.grid_size, self.block_size, params)
        return self.output_buffer

    def get_arrangement_code(self, array: probe_params.ProbeArray):
        return 0 if array.arrangement == "ox" else 1

    def get_tx_ap_bounds(self, raw_sequence: TxRxSequence):
        first_els = []
        last_els = []
        for op in raw_sequence.ops:
            ap_elems = np.argwhere(op.tx.aperture)
            first = np.min(ap_elems)-self.array_tx.start
            last = np.max(ap_elems)-self.array_tx.start
            first_els.append(first)
            last_els.append(last)
        first_els, last_els = np.asarray(first_els), np.asarray(last_els)
        probe_model = self.array_tx.to_arrus_probe()
        pos_x = probe_model.element_pos_x
        return pos_x[first_els], pos_x[last_els]

    def get_rx_ap_origin(self, raw_sequence: TxRxSequence):
        origins = []
        for op in raw_sequence.ops:
            ap_elems = np.argwhere(op.rx.aperture)
            first = np.min(ap_elems) - self.array_rx.start
            origins.append(first)
        return np.asarray(origins)

    def get_ap_center(
            self,
            aperture: arrus.ops.us4r.Aperture,
            probe: probe_params.ProbeArray
    ):
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
