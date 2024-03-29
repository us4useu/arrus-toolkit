from typing import Tuple, Iterable
import dataclasses
from dataclasses import dataclass

import numpy as np

import arrus.medium
from arrus.ops.us4r import (
    TxRxSequence, TxRx, Tx, Rx, Aperture, Pulse
)
from arrus.devices.probe import ProbeModel, ProbeModelId
from arrus.kernels.tx_rx_sequence import (
    convert_to_us4r_sequence
)
import arrus_rca_utils.probe_params as probe_params


@dataclass(frozen=True)
class RcaSequence:
    """
    :param arrangement: available values: alternate (xy or yx), same_x, same_y
    """
    sequence: TxRxSequence
    tx: probe_params.ProbeArray
    rx: probe_params.ProbeArray
    arrangement: str = "alternate"


def convert_to_system_sequence_for_metadata(
    sequences: Iterable[RcaSequence],
    metadata
):
    return convert_to_system_sequence(
        sequences=sequences,
        probe_model=metadata.context.device.probe.model,
        device_sampling_frequency=metadata.context.device.sampling_frequency
    )


def convert_to_system_sequence(
        sequences: Iterable[RcaSequence],
        probe_model: ProbeModel,
        device_sampling_frequency,
):
    seqs = []
    for s in sequences:
        if s.arrangement in {"same_x", "same_y"}:
            raw_sequence, _ = _get_system_sequence_same_arrangement(
                sequence=s.sequence, array=s.tx,
                arrangement=s.arrangement,
                device_sampling_frequency=device_sampling_frequency,
                probe_model=probe_model
            )
        elif s.arrangement == "alternate":
            raw_sequence, _ = _get_system_sequence_alternate_arrangement(
                sequence=s.sequence,
                array_tx=s.tx, array_rx=s.rx,
                device_sampling_frequency=device_sampling_frequency
            )
        else:
            raise ValueError(f"Unknown orientation: {s.arrangement}")
        seqs.append(raw_sequence)
    system_sequence = __concatenate_sequences_all(seqs)
    return __equalize_rx_aperture(system_sequence)


def _get_system_sequence_same_arrangement(
        sequence: TxRxSequence,
        array: probe_params.ProbeArray,
        arrangement: str,
        probe_model: ProbeModel,
        device_sampling_frequency: float,
):
    total_n_elements = probe_model.n_elements
    arrus_pm = array.to_arrus_probe()
    raw_sequence, tx_delay = convert_to_us4r_sequence(
        sequence=sequence,
        probe_model=arrus_pm,
        fs=device_sampling_frequency
    )
    if arrangement == "same_x":
        should_append_left = True
    elif arrangement == "same_y":
        should_append_left = False
    else:
        raise ValueError(f"Unknown arrangement: {arrangement}")
    raw_sequence = __extend_aperture(
        sequence=raw_sequence,
        n_missing_elements=total_n_elements - array.n_elements,
        is_append_left=should_append_left
    )
    return raw_sequence, tx_delay


def _get_system_sequence_alternate_arrangement(
        sequence,
        array_tx: probe_params.ProbeArray,
        array_rx: probe_params.ProbeArray,
        device_sampling_frequency: float
):
    seq_array_tx, seq_array_rx = __split_sequence_between_arrays(
        sequence=sequence, array_tx=array_tx, array_rx=array_rx
    )
    arrus_array_tx = array_tx.to_arrus_probe()
    arrus_array_rx = array_rx.to_arrus_probe()
    raw_seq_tx, init_delay = convert_to_us4r_sequence(
        sequence=seq_array_tx,
        probe_model=arrus_array_tx,
        fs=device_sampling_frequency
    )
    raw_seq_rx, _ = convert_to_us4r_sequence(
        sequence=seq_array_rx,
        probe_model=arrus_array_rx,
        fs=device_sampling_frequency
    )
    if array_rx.start < array_tx.start:
        seqs = (raw_seq_rx, raw_seq_tx)
    else:
        seqs = (raw_seq_tx, raw_seq_rx)
    return __merge_apertures(*seqs), init_delay


def __split_sequence_between_arrays(
        sequence: TxRxSequence,
        array_tx: probe_params.ProbeArray,
        array_rx: probe_params.ProbeArray
):
    array_tx_ops = []
    array_rx_ops = []
    for op in sequence.ops:
        tx: Tx = op.tx
        rx: Rx = op.rx

        empty_rx_aperture = np.zeros(array_tx.n_elements).astype(bool)
        empty_rx = dataclasses.replace(rx, aperture=empty_rx_aperture)
        array_tx_op = dataclasses.replace(op, tx=tx, rx=empty_rx)
        array_tx_ops.append(array_tx_op)

        empty_tx_aperture = np.zeros(array_rx.n_elements).astype(bool)
        empty_tx = dataclasses.replace(tx, aperture=empty_tx_aperture)
        probe_rx_op = dataclasses.replace(op, tx=empty_tx, rx=rx)
        array_rx_ops.append(probe_rx_op)

    seq_array_tx = dataclasses.replace(
        sequence,
        ops=array_tx_ops,
    )
    seq_array_rx = dataclasses.replace(
        sequence,
        ops=array_rx_ops
    )
    return seq_array_tx, seq_array_rx


def __merge_apertures(a: TxRxSequence, b: TxRxSequence):
    new_ops = []
    assert len(a.ops) == len(b.ops)

    for op1, op2 in zip(a.ops, b.ops):
        new_rx = dataclasses.replace(
            op1.rx,
            aperture=np.concatenate((op1.rx.aperture, op2.rx.aperture))
        )
        if np.sum(op1.tx.aperture) > 0:
            base_tx = op1.tx
        else:
            base_tx = op2.tx
        new_tx = dataclasses.replace(
            base_tx,
            aperture=np.concatenate((op1.tx.aperture, op2.tx.aperture))
        )
        new_op = dataclasses.replace(op1, tx=new_tx, rx=new_rx)
        new_ops.append(new_op)

    return dataclasses.replace(a, ops=new_ops)


def __extend_aperture(
        sequence: TxRxSequence, n_missing_elements: int, is_append_left: bool
):
    result = []
    empty_aperture = np.zeros(n_missing_elements).astype(bool)

    for op in sequence.ops:
        if is_append_left:
            rx_ap = np.concatenate((empty_aperture, op.rx.aperture))
            tx_ap = np.concatenate((empty_aperture, op.tx.aperture))
        else:
            rx_ap = np.concatenate((op.rx.aperture, empty_aperture))
            tx_ap = np.concatenate((op.tx.aperture, empty_aperture))

        new_rx = dataclasses.replace(op.rx, aperture=rx_ap)
        new_tx = dataclasses.replace(op.tx, aperture=tx_ap)
        new_op = dataclasses.replace(op, tx=new_tx, rx=new_rx)
        result.append(new_op)
    return dataclasses.replace(sequence, ops=result)


def __concatenate_sequences_all(seqs):
    ops = []
    for s in seqs:
        ops.extend(s.ops)
    return dataclasses.replace(seqs[0], ops=ops)


def __equalize_rx_aperture(seq: TxRxSequence):
    new_ops = []
    max_ap_size = np.max([np.sum(op.rx.aperture) for op in seq.ops])
    for op in seq.ops:
        rx = op.rx
        rx_ap_n_elements = np.sum(rx.aperture)
        padding_right = max_ap_size - rx_ap_n_elements
        assert rx.padding == (0, 0), "Currently padded RX is not supported"
        new_padding = (0, padding_right)
        new_rx = dataclasses.replace(rx, padding=new_padding)
        new_op = dataclasses.replace(op, rx=new_rx)
        new_ops.append(new_op)
    return dataclasses.replace(seq, ops=new_ops)



