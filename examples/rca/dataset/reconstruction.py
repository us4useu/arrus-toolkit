from arrus_rca_utils.reconstruction import (
    ReconstructHriRca,
    GetFramesForRange,
    Concatenate,
    get_frame_ranges,
    get_rx_aperture_size,
    PipelineSequence,
    SelectBatch,
    Slice
)
from arrus.ops.us4r import (
    TxRxSequence
)
from arrus.utils.imaging import *
import probe_params
import cupyx.scipy.ndimage


def reorder_rf(frames, aperture_size):
    return (
        GetFramesForRange(frames=frames, aperture_size=aperture_size),
        RemapToLogicalOrder()
    )


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
        DigitalDownConversion(decimation_factor=8, fir_order=64),
        ReconstructHriRca(
            y_grid=y_grid, x_grid=x_grid, z_grid=z_grid,
            probe_tx=array_tx,
            probe_rx=array_rx,
            sequence=sequence,
            min_tang=-0.5, max_tang=0.5,
            name=name,
            arrangement=aperture_arrangement,
        ),
    )


def to_bmode():
    return (
        # Concatenate along TX axis
        Concatenate(axis=0),
        Mean(axis=0),
        Squeeze(),
        EnvelopeDetection(),
        LogCompression(),
    )


def branch(steps, name=None):
    return Pipeline(steps=steps, name=name, placement="/GPU:0")


def get_pwi_reconstruction(
        y_grid, x_grid, z_grid,
        array_x: probe_params.ProbeArray, array_y: probe_params.ProbeArray,
        sequence_xy: TxRxSequence,
        sequence_yx: TxRxSequence,
        fir_taps,
        volume_dr_min=20, volume_dr_max=80,
):
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
    # Expected input:
    bmodes = Pipeline(
        name="bmode",
        steps=(
            SelectBatch([0, 1]),
            *to_bmode(),
            branch(
                steps=(
                    DynamicRangeAdjustment(min=volume_dr_min, max=volume_dr_max),
                    Squeeze(),
                )
            ),
            # (y, x, z)
            branch(
                steps=(
                    Slice(axis=0),
                    Squeeze(),
                    Transpose(),
                    Lambda(lambda data: cupyx.scipy.ndimage.median_filter(data, size=5)),
                )),
            branch(
                steps=(
                    Slice(axis=1),
                    Squeeze(),
                    Transpose(),
                    Lambda(lambda data: cupyx.scipy.ndimage.median_filter(data, size=5)),
                )),
            branch(
                steps=(
                    Slice(axis=2),
                    Squeeze(),
                    Transpose(),
                    Lambda(lambda data: cupyx.scipy.ndimage.median_filter(data, size=5)),
           ))
        ),
        placement="/GPU:0"
    )

    pipelines = [reconstruction, bmodes]

    pipeline = PipelineSequence(pipelines)
    return pipeline
