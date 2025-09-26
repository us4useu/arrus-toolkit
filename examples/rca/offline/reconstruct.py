from utils import *
import cupy as cp
import sys
import h5py


class BatchedReconstructHriRca(Operation):
    def __init__(self, **kwargs):
        self.op = ReconstructHriRca(**kwargs)

    def prepare(self, metadata):
        single_frame_input_shape = list(metadata.input_shape)
        single_frame_input_shape[0] = 1  # batch size
        single_frame_input_shape = tuple(single_frame_input_shape)

        single_batch_metadata = metadata.copy(input_shape=single_frame_input_shape)
        output_metadata = self.op.prepare(single_batch_metadata)
        return output_metadata.copy(input_shape=(metadata.input_shape[0], ) + output_metadata.input_shape[1:])

    def process(self, data):
        result = []
        for frame_rf in data:
            frame = self.op.process(frame_rf)[0]
            result.append(frame)
        return cp.stack(result)


def to_hri(
        y_grid, x_grid, z_grid,
        array_tx, array_rx,
        sequence,
        fir_taps, name=None,
        aperture_arrangement="alternate"
):
    return (
        Transpose(axes=(0, 1, 3, 2)),
        FirFilter(taps=fir_taps),
        DigitalDownConversion(decimation_factor=4, fir_order=128),
        BatchedReconstructHriRca(
            y_grid=y_grid, x_grid=x_grid, z_grid=z_grid,
            probe_tx=array_tx,
            probe_rx=array_rx,
            sequence=sequence,
            min_tang=-0.5, max_tang=0.5,
            name=name,
            arrangement=aperture_arrangement,
        ),
    )


def get_hri_reconstruction(
        y_grid, x_grid, z_grid,
        array_x: probe_params.ProbeArray, array_y: probe_params.ProbeArray,
        sequence_xy: TxRxSequence,
        sequence_yx: TxRxSequence,
        fir_taps
):
    """
    Creates a pipeline for HRI reconstruction.
    """
    seqs = sequence_xy, sequence_yx
    range_xy, range_yx = get_frame_ranges(*seqs)
    ap_size_xy, ap_size_yx = get_rx_aperture_size(*seqs)


    reconstruction = Pipeline(
        steps=(
            branch(
                name="b",
                steps=(
                    # TX=OY, RX=OX
                    *reorder_rf(frames=range_yx, aperture_size=ap_size_yx),
                    Output(),
                    *to_hri(
                        fir_taps=fir_taps,
                        y_grid=y_grid,
                        x_grid=x_grid,
                        z_grid=z_grid,
                        array_tx=array_y,
                        array_rx=array_x,
                        sequence=sequence_yx,
                    ),
                )),
            # TX=OX, RX=OY
            *reorder_rf(frames=range_xy, aperture_size=ap_size_xy),
            Output(), 
            *to_hri(
                fir_taps=fir_taps,
                y_grid=y_grid,
                x_grid=x_grid,
                z_grid=z_grid,
                array_tx=array_x, array_rx=array_y,
                sequence=sequence_xy,
            )
        ),
        placement="/GPU:0"
    )
    return reconstruction


def reconstruct_and_save(output_hdf5, dataset, x_grid, y_grid, z_grid, fir_taps):
    rf = dataset["rf"]  # (n batches, ...)
    # NOTE: this is a list of metadata, each subsequence metadata corresponds to separate probe 
    # (in that case -- we have only a single probe connected, so just take element [0]).
    metadata = dataset["metadata"]
    sequence_xy = dataset["sequence_xy"]
    sequence_yx = dataset["sequence_yx"]
    pipeline = get_hri_reconstruction(
        y_grid=y_grid, x_grid=x_grid, z_grid=z_grid,
        array_x=ARRAY_X, array_y=ARRAY_Y, # source: utils.py
        sequence_xy=sequence_xy, sequence_yx=sequence_yx,
        fir_taps=fir_taps
    ) 
    
    # As the ReconstructHriRca does not support batched processing, we need to modify 
    # metadata a little bit in order to 
    metadata = pipeline.prepare(metadata)
    
    hri_shape = metadata[0].input_shape # (batch size, nz, ny, nx)
    hri_dtype = metadata[0].dtype

    rf_shape = list(metadata[1].input_shape) # (batch size, nTX, nsamples, n elements)

    rf_shape[1] *= 2
    rf_shape = tuple(rf_shape) # (batch size, 2*nTX, nsamples, n elements), 2*nTx, because we have XY and YX transmits
    rf_dtype = metadata[1].dtype

    batch_size = metadata[0].input_shape[0]
    n_batches = rf.shape[0]
    n_frames = batch_size*n_batches

    rfs = output_hdf5.create_dataset("rf", shape=(n_frames, ) + rf_shape[1:], dtype=rf_dtype)
    hris = output_hdf5.create_dataset("hri", shape=(n_frames, ) + hri_shape[1:], dtype=hri_dtype)

    current_size = 0

    for i, batch in enumerate(rf):
        print(f"Batch: {i}/{len(rf)}", end="\r")
        output_batch = pipeline.process(cp.asarray(batch))
        hri_xy, hri_yx = output_batch[0].get(), output_batch[2].get()
        rf_xy, rf_yx = output_batch[1].get(), output_batch[3].get()
        # Average HRI for XY and YX transmissions.
        hri = np.mean((hri_xy, hri_yx), axis=0)  # (batch size, nz, ny, nx)
        # Concatenate RFs for XY and YX transmissions.
        rf_logical = np.stack((rf_xy, rf_yx))  # (2, batch size, nTx, nsamples, n elements)
        rf_logical = rf_logical.transpose((1, 0, 2, 3, 4))  # (batch size, 2, nTX, n samples, n elements)
        batch_size, _, n_tx, n_samples, n_elements = rf_logical.shape
        rf_logical = rf_logical.reshape(batch_size, -1, n_samples, n_elements)
        # Save to file.
        print("Saving batch...", end="\r")
        rfs[current_size:(current_size+batch_size)] = rf_logical
        hris[current_size:(current_size+batch_size)] = hri
        current_size += batch_size


if __name__ == "__main__":
    y_grid = np.arange(-10e-3, 10e-3, 0.2e-3)  # np.arange(-10e-3, 10e-3, 0.2e-3)  ###### VOLUME of VIEW
    x_grid = np.arange(-10e-3, 10e-3, 0.2e-3)  # np.arange(-10e-3, 10e-3, 0.2e-3)
    z_grid = np.arange(0e-3, 70e-3, 0.2e-3)    # np.arange(0e-3, 30e-3, 0.1e-3)

    if len(sys.argv) < 2:
        raise ValueError("The name of the raw dataset should be provided.")
    
    print("Reading data")
    name = sys.argv[1]
    filename = f"{name}_raw_dataset.pkl"
    with open(filename, "rb") as f:
        dataset = pickle.load(f)

    center_frequency = dataset["metadata"].context.sequence.ops[0].tx.excitation.center_frequency
    sampling_frequency = dataset["metadata"].context.device.sampling_frequency
    fir_taps = scipy.signal.firwin(
        numtaps=64, cutoff=np.array([0.5, 1.5]) * center_frequency,
        pass_zero="bandpass", fs=sampling_frequency
    )
    
    output_filename = f"{name}_beamformed_dataset.h5"
    print(f"Reconstructing and saving to file: {output_filename}")
    with h5py.File(output_filename, "w") as f:
        reconstruct_and_save(output_hdf5=f, dataset=dataset, x_grid=x_grid, y_grid=y_grid, z_grid=z_grid, fir_taps=fir_taps)
    print()
    print("Done")
