import numpy as np


def get_delays(tx_focus, tx_ang_zx, tx_ang_zy, probe_model, speed_of_sound):
    pitch = probe_model.pitch
    n_elements = probe_model.n_elements
    return get_delays_raw(tx_focus, tx_ang_zx, tx_ang_zy, pitch,
                          n_elements, speed_of_sound)


def get_delays_raw(tx_focus, tx_ang_zx, tx_ang_zy, pitch, n_elements,
                   speed_of_sound):
    n_x = int(round(math.sqrt(n_elements)))
    assert n_x ** 2 == n_elements, "Only square probes are supported"
    # Assuming, that here are 4 groups of 256 elements separated by a single
    # dead row.
    n_y = n_x + 3
    # Positions of the elements. Assuming that the coordinate system origin
    # (0, 0, 0) is in the center of probe.
    x = (np.arange(0, n_x) - n_x//2 + 0.5)*pitch  # [m]
    y = (np.arange(0, n_y) - n_y//2 + 0.5)*pitch  # [m]
    x = np.atleast_2d(x)  # (1, n_x)
    y = np.atleast_2d(y).T  # (n_y, 1)

    # Assuming all elements are at z = 0
    z = np.zeros((n_y, n_x), dtype=np.float64)
    delays = []
    center_delays = []

    for f, ang_zx, ang_zy in zip(tx_focus, tx_ang_zx, tx_ang_zy):
        # Convert plane inclinations to the spherical angles
        ang_zenith = np.hypot(np.tan(ang_zx), np.tan(ang_zy))
        ang_zenith = np.arctan(ang_zenith)
        ang_azimuth = np.arctan2(np.tan(ang_zy), np.tan(ang_zx))
        # NOTE: for moving TX aperture, tx centers are needed.
        if np.isinf(f):
            # Plane wave transmission
            tx_dist = z*np.cos(ang_zenith) \
                      + (x*np.cos(ang_azimuth)+y*np.sin(ang_azimuth)) \
                        * np.sin(ang_zenith)
            tx_center_distance = 0  # Note: this will not work with the moving
                                    # aperture.
        else:
            # Note: assuming the center of aperture in (0, 0, 0)
            z_foc = f*np.cos(ang_zenith)
            x_foc = f*np.sin(ang_zenith)*np.cos(ang_azimuth)  # + tx ap center x
            y_foc = f*np.sin(ang_zenith)*np.sin(ang_azimuth)  # + tx ap center y
            tx_dist = np.sqrt((x_foc-x)**2 + (y_foc-y)**2 + (z_foc-z)**2)
            tx_center_distance = 0
        delay = tx_dist/speed_of_sound
        if f > 0:
            delay = delay*(-1)
        center_delay = tx_center_distance/speed_of_sound
        delays.append(delay)
        center_delays.append(center_delay)
    delays = np.stack(delays)
    center_delays = np.stack(center_delays)

    min_delays = np.min(delays)
    delays = delays-min_delays
    center_delays = center_delays-min_delays

    tx_center_delay = np.max(center_delays)
    for i in range(delays.shape[0]):
        delays[i] = delays[i] - center_delays[i] + tx_center_delay
    delays = np.delete(delays, (n_x // 4, n_x // 2 + 1, 3 * n_x // 4 + 2),
        axis=1)
    return delays.reshape(-1, n_elements)


def get_sequence(
        probe_model, speed_of_sound,
        tx_focus, tx_ang_zx, tx_ang_zy,
        tx_frequency, n_periods, pri, sample_range
):
    n_elements = probe_model.n_elements
    delays = get_delays(
        tx_focus=tx_focus,
        tx_ang_zx=tx_ang_zx,
        tx_ang_zy=tx_ang_zy,
        probe_model=probe_model,
        speed_of_sound=speed_of_sound
    )
    ops = [
        TxRx(
            tx=Tx(
                aperture=[True] * n_elements,  # Full TX aperture.
                excitation=Pulse(center_frequency=tx_frequency, n_periods=n_periods,
                                inverse=False),
                delays=d
            ),
            rx=Rx(
                # Full RX aperture
                # (note: will be automatically split to multiple RX sub-apertures.)
                aperture=[True] * n_elements,
                sample_range=sample_range
            ),
            pri=pri
    ) for d in delays]
    return TxRxSequence(
        ops=ops,
        tgc_curve=[],  # Will be set later.
        # How many times this sequence should be repeated before
        # starting data transfer from us4R to host PC.
        n_repeats=1,
    )


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



def __append_to_metadata(metadata, tx_focus, tx_ang_zx, tx_ang_zy):
    metadata = metadata.copy()
    metadata.data_description.custom["matrix_array_tx_focus"] = tx_focus
    metadata.data_description.custom["matrix_array_tx_ang_zx"] = tx_ang_zx
    metadata.data_description.custom["matrix_array_tx_ang_zy"] = tx_ang_zy
    return metadata


def get_imaging(
        sequence,
        tx_focus, tx_ang_zx, tx_ang_zy,
        x_grid, y_grid, z_grid,
        decimation_factor=30, decimation_fir_order=64,
):
    pipeline = Pipeline(
        steps=(
            # NOTE: Currently, the tx_focus, tx_ang_zx and tx_ang_zy
            # are parameters specific for this gui4us configuration,
            # AND ARE NOT STORED in the ARRUS metadat by default.
            # As for now, the TX focus and angles are stored in the metadata
            # property "custom".
            # Since ARRUS 0.13.0, all the parameters will be stored as part
            # of the TX/RX sequence.
            Lambda(lambda data: data, lambda metadata: __append_to_metadata(
                metadata=metadata,
                tx_focus=tx_focus,
                tx_ang_zx=tx_ang_zx,
                tx_ang_zy=tx_ang_zy
            )),
            Output(),  # Output raw channel data
            RemapToLogicalOrder(),
            Transpose(axes=(0, 1, 3, 2)),
            Reshape(1, len(sequence.ops), 32, 32, n_samples),
            BandpassFilter(),
            DigitalDownConversion(
                decimation_factor=decimation_factor,
                fir_order=decimation_fir_order
            ),
            ReconstructLri3D(
                x_grid=x_grid, y_grid=y_grid, z_grid=z_grid,
                tx_foc=tx_focus,
                tx_ang_zx=tx_ang_zx, tx_ang_zy=tx_ang_zy,
                speed_of_sound=speed_of_sound),
            Squeeze(),
            EnvelopeDetection(),
            LogCompression(),
            Pipeline(
                steps=(
                    Slice(axis=0),
                    Transpose(),
                    # axis 0 -> OY -> y = 0 plane -> OXZ
                ),
                placement="/GPU:0"
            ),
            Slice(axis=1),  # axis 1 -> OX -> x = 0 -> plane OYZ
            Transpose(),
        ),
        placement="/GPU:0"
    )
    return pipeline


def get_rf_imaging():
    pipeline = Pipeline(
        steps=(
            RemapToLogicalOrder(),
            SelectFrames([0]),
            Squeeze(),
            Reshape(32, 32, n_samples),
            Pipeline(
                steps=(
                    Slice(axis=0),
                    # axis 0 -> OY -> y = 0 plane -> OXZ
                ),
                placement="/GPU:0"
            ),
            Slice(axis=1),
            # axis 1 -> OX -> x = 0 -> plane OYZ
        ),
        placement="/GPU:0"
    )
    return pipeline