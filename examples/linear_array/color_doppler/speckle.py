# SPDX-FileCopyrightText: Copyright (c) 2026, Chris von Csefalvay (HCLTech).
# SPDX-License-Identifier: Apache-2.0

"""Speckle-reducing anisotropic diffusion for the B-mode image.

Ultrasound speckle is not noise added to the image, it is the image: the
interference pattern of scatterers too small to resolve. Averaging it away with
a blur takes the edges with it, which is what makes plain smoothing useless
here. SRAD (Yu & Acton, 2002) diffuses only where the local statistics look
like speckle and stops where they look like a boundary, so a homogeneous region
smooths while the wall between two of them stays put.

The test is the instantaneous coefficient of variation: fully developed speckle
has a known one -- about 0.52 for an envelope amplitude, 1.0 for an intensity
-- and a pixel whose neighbourhood varies much more than that is standing on
structure. That comparison is what ``q0`` sets; the diffusion coefficient falls
towards zero as the local variation exceeds it.

This runs inside the arrus pipeline on the envelope, **before** log
compression: the speckle model is multiplicative on the envelope, and the log
turns it into an additive one the coefficient of variation no longer describes.
The array package is taken from the data, so the same code runs on a CuPy array
on the GPU (where the pipeline puts it) and on a NumPy array in a test.

Every parameter is in the application's YAML under ``arrus_source.srad``; see
the README. The defaults are deliberately mild: this is an aid to reading the
image, and a filter strong enough to invent a smooth boundary is worse than no
filter at all.
"""

from __future__ import annotations

import math
import traceback
from typing import Any

import numpy as np

# Yu & Acton's own discretisation is a four-neighbour scheme, so the step is
# divided by four and anything above 0.25 can grow rather than diffuse.
MAX_STEP = 0.25
# For estimating the speckle scale from the frame: the image is cut into blocks
# this many pixels on a side, and the scale is a low percentile of their
# coefficients of variation -- low, because a block that straddles a boundary
# varies more than speckle does, and overestimating the scale is what turns
# this filter into a blur.
SCALE_BLOCK_PX = 16
SCALE_PERCENTILE = 25.0
# Fully developed speckle sits near 0.52 in amplitude; a frame that is mostly
# structure or mostly dead gain can estimate far from it, so the estimate is
# held to a range where the filter still behaves.
SCALE_LIMITS = (0.05, 1.5)
DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "iterations": 6,
    "step": 0.15,
    "rho": 0.1,
    "q0": "auto",
}


class SpeckleFilterError(ValueError):
    """Raised when the speckle filter is configured with something unusable."""


def _array_module(data: Any):
    """NumPy or CuPy, whichever owns this array."""
    try:
        import cupy
    except ImportError:  # pragma: no cover - CuPy ships in the application image.
        return np
    return cupy.get_array_module(data)


def speckle_scale(
    image: Any,
    block_px: int = SCALE_BLOCK_PX,
    percentile: float = SCALE_PERCENTILE,
) -> float:
    """Estimate the coefficient of variation of this frame's speckle.

    A low percentile over blocks rather than the whole frame's variation: a
    B-mode image is mostly structure, and the frame-wide figure would say the
    speckle varies far more than it does. The filter then treats real edges as
    speckle and smooths them away, which is the one failure worth designing
    against.
    """
    xp = _array_module(image)
    data = image.astype(xp.float32, copy=False)
    height, width = data.shape[-2:]
    rows, columns = height // block_px, width // block_px
    if rows < 1 or columns < 1:
        # Smaller than one block: the whole frame is the only estimate there is.
        mean = float(data.mean())
        return _clamp(float(data.std()) / mean if mean > 0 else SCALE_LIMITS[0])
    trimmed = data[: rows * block_px, : columns * block_px]
    blocks = trimmed.reshape(rows, block_px, columns, block_px)
    means = blocks.mean(axis=(1, 3))
    deviations = blocks.std(axis=(1, 3))
    variation = deviations / xp.maximum(means, 1e-12)
    # Blocks with no signal say nothing about speckle; they would drag the
    # percentile to zero and switch the filter off.
    lit = variation[means > float(means.max()) * 0.01]
    if lit.size == 0:
        return SCALE_LIMITS[0]
    return _clamp(float(xp.percentile(lit, percentile)))


def _clamp(value: float) -> float:
    low, high = SCALE_LIMITS
    if not math.isfinite(value):
        return low
    return min(max(value, low), high)


# On the GPU the arithmetic below is thirty-odd elementwise operations per
# iteration, each its own kernel launch, and at this image size the launches
# cost far more than the arithmetic: 14 ms a frame against a 26 ms budget at
# 38 fps. The same two steps fused into two kernels cost a fraction of that.
# They must stay arithmetically identical to the NumPy path above, which
# test_the_two_implementations_agree checks against it.
_CUPY_KERNELS: dict[str, Any] = {}

_COEFFICIENT_BODY = """
    int y = i / w;
    int x = i - y * w;
    T here = img[i];
    T north = img[(y > 0 ? y - 1 : 0) * w + x] - here;
    T south = img[(y < h - 1 ? y + 1 : h - 1) * w + x] - here;
    T west = img[y * w + (x > 0 ? x - 1 : 0)] - here;
    T east = img[y * w + (x < w - 1 ? x + 1 : w - 1)] - here;
    T intensity = here > (T)1e-12 ? here : (T)1e-12;
    T gradient = (north * north + south * south + west * west + east * east)
                 / (intensity * intensity);
    T laplacian = (north + south + west + east) / intensity;
    T numerator = (T)0.5 * gradient - (T)0.0625 * laplacian * laplacian;
    T denominator = (T)1.0 + (T)0.25 * laplacian;
    denominator = denominator * denominator;
    if (denominator < (T)1e-12) denominator = (T)1e-12;
    T variation = numerator / denominator;
    T scale = q0sq * ((T)1.0 + q0sq);
    if (scale < (T)1e-12) scale = (T)1e-12;
    T value = (T)1.0 / ((T)1.0 + (variation - q0sq) / scale);
    c = value < (T)0.0 ? (T)0.0 : (value > (T)1.0 ? (T)1.0 : value);
"""

_UPDATE_BODY = """
    int y = i / w;
    int x = i - y * w;
    T here = img[i];
    int south_index = (y < h - 1 ? y + 1 : h - 1) * w + x;
    int east_index = y * w + (x < w - 1 ? x + 1 : w - 1);
    T divergence = c[i] * (img[(y > 0 ? y - 1 : 0) * w + x] - here)
                 + c[south_index] * (img[south_index] - here)
                 + c[i] * (img[y * w + (x > 0 ? x - 1 : 0)] - here)
                 + c[east_index] * (img[east_index] - here);
    T value = here + step * (T)0.25 * divergence;
    out = value < (T)0.0 ? (T)0.0 : value;
"""


def _cupy_kernels() -> tuple[Any, Any]:
    """The fused steps, compiled once and kept."""
    if not _CUPY_KERNELS:
        import cupy

        _CUPY_KERNELS["coefficient"] = cupy.ElementwiseKernel(
            "raw T img, int32 h, int32 w, T q0sq", "T c",
            _COEFFICIENT_BODY, "srad_coefficient",
        )
        _CUPY_KERNELS["update"] = cupy.ElementwiseKernel(
            "raw T img, raw T c, int32 h, int32 w, T step", "T out",
            _UPDATE_BODY, "srad_update",
        )
    return _CUPY_KERNELS["coefficient"], _CUPY_KERNELS["update"]


def _srad_cupy(image: Any, iterations: int, step: float, rho: float, scale: float) -> Any:
    """The same diffusion, two kernel launches an iteration instead of thirty."""
    import cupy

    coefficient_of, update = _cupy_kernels()
    out = image.astype(cupy.float32, copy=True)
    height, width = out.shape
    coefficient = cupy.empty_like(out)
    result = cupy.empty_like(out)
    for iteration in range(iterations):
        q0_squared = (scale * math.exp(-rho * iteration)) ** 2
        coefficient_of(out, height, width, cupy.float32(q0_squared), coefficient)
        update(out, coefficient, height, width, cupy.float32(step), result)
        out, result = result, out
    return out


def srad(
    image: Any,
    iterations: int = DEFAULTS["iterations"],
    step: float = DEFAULTS["step"],
    rho: float = DEFAULTS["rho"],
    q0: float | None = None,
) -> Any:
    """Return the frame with its speckle diffused, its boundaries left alone.

    ``q0`` is the speckle scale to compare against; None estimates it from this
    frame. It decays by ``exp(-rho * n)`` over the iterations, as in the paper:
    later passes demand stronger evidence of homogeneity, so the filter
    smooths hardest early and then leaves the image be.
    """
    if iterations <= 0:
        return image
    xp = _array_module(image)
    out = image.astype(xp.float32, copy=True)
    shape = out.shape
    if out.ndim != 2:
        # The pipeline can carry singleton axes around the image -- the capture
        # callback squeezes them too -- and they are not a reason to refuse.
        if math.prod(shape[:-2]) != 1:
            raise SpeckleFilterError(f"the speckle filter needs a 2-D frame, got {shape}")
        out = out.reshape(shape[-2:])
    scale = float(q0) if q0 is not None else speckle_scale(out)
    if xp is not np:
        return _srad_cupy(out, iterations, step, rho, scale).reshape(shape)

    for iteration in range(iterations):
        # The speckle the filter is willing to smooth, shrinking as it goes.
        q0_squared = (scale * math.exp(-rho * iteration)) ** 2
        padded = xp.pad(out, 1, mode="edge")
        north = padded[:-2, 1:-1] - out
        south = padded[2:, 1:-1] - out
        west = padded[1:-1, :-2] - out
        east = padded[1:-1, 2:] - out

        intensity = xp.maximum(out, 1e-12)
        gradient_squared = (north**2 + south**2 + west**2 + east**2) / intensity**2
        laplacian = (north + south + west + east) / intensity
        numerator = 0.5 * gradient_squared - 0.0625 * laplacian**2
        denominator = (1.0 + 0.25 * laplacian) ** 2
        variation_squared = numerator / xp.maximum(denominator, 1e-12)

        # 1 where the neighbourhood varies like speckle, towards 0 where it
        # varies more than speckle can account for: an edge.
        coefficient = 1.0 / (
            1.0
            + (variation_squared - q0_squared) / max(q0_squared * (1.0 + q0_squared), 1e-12)
        )
        coefficient = xp.clip(coefficient, 0.0, 1.0)

        # Each neighbour is weighted by the coefficient on its own side, which
        # is what keeps the scheme from leaking across a boundary.
        padded_coefficient = xp.pad(coefficient, 1, mode="edge")
        divergence = (
            coefficient * north
            + padded_coefficient[2:, 1:-1] * south
            + coefficient * west
            + padded_coefficient[1:-1, 2:] * east
        )
        out = out + (step / 4.0) * divergence
        # An envelope is an amplitude: diffusion must not drive it negative.
        out = xp.maximum(out, 0.0)
    return out.reshape(shape)


def srad_settings(config: dict[str, Any] | None) -> dict[str, Any] | None:
    """Read the ``srad`` section, or None when there is nothing to do.

    Raises rather than falling back to a default, because a filter that was
    asked for and silently did not run looks exactly like one that did nothing
    useful, and the two want opposite responses.
    """
    if not config:
        return None
    unknown = set(config) - set(DEFAULTS)
    if unknown:
        raise SpeckleFilterError(
            f"unknown arrus_source.srad settings: {', '.join(sorted(unknown))}; "
            f"there is {', '.join(sorted(DEFAULTS))}"
        )
    settings = {**DEFAULTS, **config}
    if not settings["enabled"]:
        return None

    iterations = int(settings["iterations"])
    if iterations < 1:
        raise SpeckleFilterError(f"arrus_source.srad.iterations must be at least 1, not {iterations}")
    step = float(settings["step"])
    if not 0.0 < step <= MAX_STEP:
        raise SpeckleFilterError(
            f"arrus_source.srad.step must be within (0, {MAX_STEP}], not {step}; "
            "a larger step makes the scheme unstable"
        )
    rho = float(settings["rho"])
    if rho < 0.0:
        raise SpeckleFilterError(f"arrus_source.srad.rho must not be negative, not {rho}")
    q0 = settings["q0"]
    if q0 in (None, "", "auto"):
        q0 = None
    else:
        q0 = float(q0)
        if q0 <= 0.0:
            raise SpeckleFilterError(f"arrus_source.srad.q0 must be positive, not {q0}")
    return {"iterations": iterations, "step": step, "rho": rho, "q0": q0}


class Srad:
    """The filter as a pipeline step: callable on one frame, holding its settings.

    Two things this has to get right, because it runs inside an acquisition:

    **It compiles before the first frame.** The fused kernels are built by NVRTC
    on first use, which takes about 0.7 s -- some thirty frames at 38 fps. Inside
    the pipeline that overruns the boards' rx buffer and the scheme stops, so
    warm_up() is called while the pipeline is being assembled, before the scheme
    is uploaded.

    **It never raises.** An exception in a pipeline step does not skip a frame,
    it stops them arriving, with nothing on the terminal to say why. So a
    failure here reports itself once and passes the frame through unfiltered: a
    grainier image is a far better outcome than a frozen one.
    """

    def __init__(
        self,
        iterations: int = DEFAULTS["iterations"],
        step: float = DEFAULTS["step"],
        rho: float = DEFAULTS["rho"],
        q0: float | None = None,
    ):
        self.iterations = iterations
        self.step = step
        self.rho = rho
        self.q0 = q0
        self.failures = 0

    def warm_up(self, shape: tuple[int, int] = (512, 512)) -> bool:
        """Compile the kernels now, on a frame nobody is waiting for.

        The rehearsal has to look like the real thing. CuPy compiles per
        argument layout, and the pipeline hands over a *transposed* view -- what
        Transpose() leaves behind -- so warming up on a contiguous array leaves
        the strided copy to compile inside the acquisition (measured: 0.6 s).
        The speckle-scale estimate is a second set of kernels again, so this
        runs with the configured q0 rather than a convenient one.
        """
        try:
            import cupy
        except ImportError:  # pragma: no cover - no GPU, no kernels to compile.
            return False
        try:
            height, width = shape
            sample = (
                cupy.arange(height * width, dtype=cupy.float32).reshape(width, height).T
            )
            srad(sample, self.iterations, self.step, self.rho, self.q0)
            cupy.cuda.Stream.null.synchronize()
        except Exception as error:  # noqa: BLE001 - imaging must start regardless.
            print(f"[srad] could not prepare the speckle filter: {error}", flush=True)
            return False
        print(
            f"[srad] speckle filter ready: {self.iterations} iterations, step {self.step}, "
            f"q0 {'auto' if self.q0 is None else self.q0}",
            flush=True,
        )
        return True

    def __call__(self, data: Any) -> Any:
        try:
            return srad(data, self.iterations, self.step, self.rho, self.q0)
        except Exception as error:  # noqa: BLE001 - see the class docstring.
            self.failures += 1
            if self.failures == 1:
                print(f"[srad] filtering failed, frames pass through unfiltered: {error}", flush=True)
                traceback.print_exc()
            return data

    def __repr__(self) -> str:  # pragma: no cover - for logs and test failures.
        return (
            f"Srad(iterations={self.iterations}, step={self.step}, "
            f"rho={self.rho}, q0={self.q0})"
        )
