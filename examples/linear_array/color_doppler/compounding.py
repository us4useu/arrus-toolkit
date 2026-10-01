# SPDX-FileCopyrightText: Copyright (c) 2026, Chris von Csefalvay (HCLTech).
# SPDX-License-Identifier: Apache-2.0
#
# Adapted from holohub applications/ultrasound_guidance/arrus_source.py (CompoundingSplit,
# compounding_steps) for the arrus-toolkit colour Doppler example.

"""Coherent/incoherent compounding of the beamformed transmits into one B-mode envelope.

Beamforming leaves one image per transmit. Summing those on the IQ data, before envelope
detection, is coherent: the wavefronts add where they agree, which sharpens the image -- and for
synthetic transmit aperture (STA) it is not optional, since a single element's transmit is not an
image of anything on its own. Summing after envelope detection is incoherent: the phase is gone,
so nothing sharpens, but the speckle patterns differ between the transmits and averaging them
evens the speckle out.

Splitting the transmits between the two is the useful middle: the array is reshaped to
``(n_incoherent, n_coherent, x, z)``, the ``n_coherent`` axis is averaged on IQ, and the
``n_incoherent`` images that leaves are averaged after envelope detection.
"""
from typing import Optional, Tuple

from arrus.utils.imaging import EnvelopeDetection, Lambda, Mean


class CompoundingSplit:
    """Reshapes (n_seq, n_tx, x, z) to (n_incoherent, n_coherent, x, z).

    Either count may be None ("auto"): whatever the other one leaves. The shape is checked when the
    scheme is uploaded rather than when the first frame arrives: an exception in a running pipeline
    stops the frames with nothing on the terminal.
    """

    def __init__(self, n_coherent: Optional[int], n_incoherent: Optional[int]):
        if n_coherent is None and n_incoherent is None:
            raise ValueError("n_coherent and n_incoherent cannot both be auto; state one of them")
        for name, value in (("n_coherent", n_coherent), ("n_incoherent", n_incoherent)):
            if value is not None and value < 1:
                raise ValueError(f"{name} must be at least 1, not {value}")
        self.n_coherent = n_coherent
        self.n_incoherent = n_incoherent
        self.shape: Optional[Tuple[int, ...]] = None

    def prepare(self, const_metadata):
        n_seq, n_tx, *image = const_metadata.input_shape
        total = int(n_seq)*int(n_tx)
        coherent, incoherent = self.n_coherent, self.n_incoherent
        if coherent is None:
            coherent = total // incoherent
        if incoherent is None:
            incoherent = total // coherent
        if coherent*incoherent != total:
            divisors = [d for d in range(1, total + 1) if total % d == 0]
            raise ValueError(
                f"n_coherent x n_incoherent must be the number of transmits, {total} "
                f"({n_seq} x {n_tx}), not {coherent} x {incoherent} = {coherent*incoherent}. "
                f"Whole factors of {total}: {', '.join(str(d) for d in divisors)}")
        self.shape = (incoherent, coherent, *image)
        print(f"[compounding] {total} transmits as {incoherent} x {coherent}: {coherent} summed on IQ, "
              f"{incoherent} after envelope detection", flush=True)
        return const_metadata.copy(input_shape=self.shape)

    def __call__(self, data):
        return data.reshape(self.shape)


def compounding_steps(n_coherent: Optional[int] = None, n_incoherent: Optional[int] = 1) -> tuple:
    """The pipeline steps from the beamformed transmits (ReconstructLri output) to one envelope image.

    The default (n_coherent auto, n_incoherent 1) sums all the transmits on IQ (coherent compounding),
    as arrus' own get_bmode_imaging does; n_coherent=1, n_incoherent=None is fully incoherent.
    """
    split = CompoundingSplit(n_coherent, n_incoherent)
    return (
        Lambda(split, split.prepare),
        Mean(axis=1),  # n_coherent, summed on IQ: sharpness
        EnvelopeDetection(),
        Mean(axis=0),  # n_incoherent, summed after the envelope detection: speckle evens out
    )
