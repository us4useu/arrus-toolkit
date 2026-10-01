import math
import os
import cupy as cp
import numpy as np
import scipy.signal
import cupyx.scipy.ndimage
import cupyx.scipy.signal
from arrus.utils.imaging import Operation, ParameterDef, Box, Unit
from typing import Dict, Sequence
from numbers import Number


def warm_up(operation, const_metadata):
    """Runs the operation once on zeros of its input shape, i.e. compiles its GPU kernels now.

    ARRUS warms the operations up while the pipeline is prepared (Operation.initialize), but not
    the ones inside a nested Pipeline -- and every Doppler operation here is nested. Without this,
    their kernels are compiled while the first frame is processed, which takes longer than the
    us4R watchdog allows (1 s by default): the device stops right after starting.
    """
    dummy = cp.zeros(const_metadata.input_shape, dtype=const_metadata.dtype)
    operation.process(dummy)
    cp.cuda.Device().synchronize()


class CreateDopplerFrame(Operation):
    """
    Creates the final ColorDoppler frame.
    """


    def __init__(
            self,
            color_dynamic_range=(0, np.pi/2),
            power_dynamic_range=(0, 80),
            frame_type="color",
            name: str = None
        ):

        super().__init__(name=name)
        self.color_dr_min, self.color_dr_max = color_dynamic_range
        self.power_dr_min, self.power_dr_max = power_dynamic_range
        if frame_type == "color":
            self.frame_type_nr = 0
        elif frame_type == "power":
            self.frame_type_nr = 1
        else:
            raise ValueError(f"Unsupported doppler frame type: {frame_type}")

    def prepare(self, const_metadata):
        input_shape = const_metadata.input_shape
        input_dtype = const_metadata.dtype
        self.output_buffer = cp.zeros(input_shape[1:], dtype=input_dtype)
        warm_up(self, const_metadata)
        return const_metadata.copy(input_shape=self.output_buffer.shape)

    def process(self, data):
        color_doppler = data[0]
        power_doppler = data[1]

        self.output_buffer[:] = data[self.frame_type_nr]
        # Compute
        mask_color = cp.logical_and(
            cp.abs(color_doppler) > self.color_dr_min,
            cp.abs(color_doppler) < self.color_dr_max
        )
        mask_power = cp.logical_and(
            power_doppler > self.power_dr_min,
            power_doppler < self.power_dr_max
        )
        mask = cp.logical_not(cp.logical_and(mask_color, mask_power))
        self.output_buffer[mask] = None
        return self.output_buffer

    def get_parameters(self) -> Dict[str, ParameterDef]:
        params = [
            ParameterDef(
                name="power_dr_min",
                space=Box(
                    shape=(1,),
                    dtype=np.float32,
                    unit=Unit.dB,
                    low=-np.inf,
                    high=np.inf
                ),
            ),
            ParameterDef(
                name="power_dr_max",
                space=Box(
                    shape=(1,),
                    dtype=np.float32,
                    unit=Unit.dB,
                    low=-np.inf,
                    high=np.inf
                ),
            ),
            ParameterDef(
                name="color_dr_min",
                space=Box(
                    shape=(1,),
                    dtype=np.float32,
                    unit=Unit.rad,
                    low=-np.inf,
                    high=np.inf
                ),
            ),
            ParameterDef(
                name="color_dr_max",
                space=Box(
                    shape=(1,),
                    dtype=np.float32,
                    unit=Unit.rad,
                    low=-np.inf,
                    high=np.inf
                ),
            )
        ]
        return dict(((p.name, p) for p in params))

    def set_parameter(self, key: str, value: Sequence[Number]):
        if not hasattr(self, key):
            raise ValueError(f"{type(self).__name__} has no {key} parameter.")
        if isinstance(value, np.ndarray):
            value = value.item()
        setattr(self, key, value)

    def get_parameter(self, key: str) -> Sequence[Number]:
        if not hasattr(self, key):
            raise ValueError(f"{type(self).__name__} has no {key} parameter.")
        return getattr(self, key)


class ReconstructDoppler(Operation):

    def __init__(self, name: str = None):
        super().__init__(name=name)

    def prepare(self, metadata):
        current_dir = os.path.dirname(__file__)
        doppler_src = open(os.path.join(current_dir, "doppler.cc")).read()
        self.doppler = cp.RawKernel(doppler_src, "doppler")
        self.nframes, self.nx, self.nz = metadata.input_shape
        self.output_dtype = cp.float32
        self.output_shape = (2, self.nx, self.nz)  # color, power
        self.output = cp.zeros(self.output_shape, dtype=self.output_dtype)
        self.block = (32, 32)
        self.grid = math.ceil(self.nz/self.block[1]), math.ceil(self.nx/self.block[0])

        op = metadata.context.sequence.ops[0]  # Reference TX
        self.angle = op.tx.angle
        self.pri = op.pri
        self.tx_frequency = op.tx.excitation.center_frequency
        self.c = op.tx.speed_of_sound
        self.scale = self.c/(2*np.pi*self.pri*self.tx_frequency*2*math.cos(self.angle))
        warm_up(self, metadata)
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
        result[1] = 10*cp.log10(result[1])  # [dB]
        return result


class FilterWallClutter(Operation):

    def __init__(self, wn, n  , ftype="fir", btype="highpass"):
        self.wn = wn
        self.n = n
        self.ftype = ftype
        self.btype = btype

    def prepare(self, metadata):

        if self.ftype in {"butter", "cheby1", "cheby2", "ellip", "bessel"}:
            self.ba = scipy.signal.iirfilter(
                self.n,
                self.wn,
                rp=10,
                rs=100,
                ftype=self.ftype,
                btype=self.btype,
                output="ba",
            )
        elif self.ftype == "fir":
            if self.n % 2 == 0:
                self.actual_n = self.n+1
            else:
                self.actual_n = self.n
            b = scipy.signal.firwin(
                self.actual_n,
                self.wn,
                pass_zero=False,
            )
            a = np.zeros(b.shape)
            a[0] = 1
            self.ba = (b, a)
        else:
            raise ValueError(
                "\n"
                "   Bad ftype value.\n"
                "   Should be one of the following: \n"
                "       fir, butter, cheby1, cheby2, ellip, bessel."
            )
        self.ba = cp.array(self.ba)
        warm_up(self, metadata)
        return metadata

    def process(self, data):
        output = cupyx.scipy.ndimage.convolve1d(data, self.ba[0], axis=0)
        output = output.astype("complex64")
        return output


def _scalar(value):
    """A parameter value from the GUI (a 1-element array) -> a Python scalar."""
    if isinstance(value, (np.ndarray, cp.ndarray)):
        return value.item()
    return value


class Persistence(Operation):
    """
    Persistence: outputs the average of the last ``n_frames`` input frames (e.g. the Doppler estimates),
    which smooths the frame-to-frame fluctuations of the colour/power Doppler display.

    The frames are kept in a ring buffer on the GPU; until ``n_frames`` frames have arrived, the average of
    the frames received so far is returned. ``n_frames`` is a pipeline parameter (the control panel);
    a change takes effect with the next frame (the history is cleared).
    """
    MAX_N_FRAMES = 32

    def __init__(self, n_frames: int = 5, name: str = None):
        super().__init__(name=name)
        self.n_frames = self._validate(n_frames)
        self.buffer = None
        self._buffer_n_frames = None
        self._position = 0
        self._count = 0

    def _validate(self, n_frames):
        n_frames = int(round(float(_scalar(n_frames))))
        if not 1 <= n_frames <= self.MAX_N_FRAMES:
            raise ValueError(f"Persistence: n_frames should be in [1, {self.MAX_N_FRAMES}], got {n_frames}")
        return n_frames

    def prepare(self, const_metadata):
        self.input_shape = tuple(const_metadata.input_shape)
        self.dtype = const_metadata.dtype
        self._reset()
        return const_metadata

    def _reset(self):
        self.buffer = cp.zeros((self.n_frames, *self.input_shape), dtype=self.dtype)
        self._buffer_n_frames = self.n_frames
        self._position = 0
        self._count = 0

    def initialize(self, data):
        # The pipeline's warm-up call: must not leave the dummy frame in the history.
        result = self.process(data)
        self._reset()
        return result

    def process(self, data):
        if self._buffer_n_frames != self.n_frames:
            # n_frames changed (set_parameter): resize here, on the processing thread.
            self._reset()
        self.buffer[self._position] = data
        self._position = (self._position + 1) % self.n_frames
        self._count = min(self._count + 1, self.n_frames)
        # While filling up (after a reset), the frames are in the slots [0, count).
        return self.buffer[:self._count].mean(axis=0)

    def get_parameters(self) -> Dict[str, ParameterDef]:
        return {
            "n_frames": ParameterDef(
                name="n_frames",
                space=Box(shape=(1,), dtype=np.int32, low=1, high=self.MAX_N_FRAMES),
            ),
        }

    def set_parameter(self, key: str, value: Sequence[Number]):
        if key != "n_frames":
            raise ValueError(f"{type(self).__name__} has no {key} parameter.")
        self.n_frames = self._validate(value)

    def get_parameter(self, key: str) -> Sequence[Number]:
        if key != "n_frames":
            raise ValueError(f"{type(self).__name__} has no {key} parameter.")
        return self.n_frames


class SvdClutterFilter(Operation):
    """
    SVD clutter (wall) filter of a Doppler ensemble.

    Input/output: the ensemble of beamformed IQ frames, shape (n_frames, x, z), complex (the same as
    FilterWallClutter). The ensemble is arranged as the Casorati matrix S (n_frames, x*z); its temporal
    singular vectors are the eigenvectors of the small (n_frames, n_frames) matrix S S^H. The ``n_tissue``
    components with the largest singular values (the strong, slowly moving tissue) and the ``n_noise``
    ones with the smallest (the noise) are removed:

        S_filtered = U_kept U_kept^H S

    Both counts are pipeline parameters (the control panel).
    """

    def __init__(self, n_tissue: int = 3, n_noise: int = 0, name: str = None):
        super().__init__(name=name)
        self.n_tissue = int(n_tissue)
        self.n_noise = int(n_noise)
        self.n_ensemble = None

    def prepare(self, metadata):
        self.n_ensemble, *image = metadata.input_shape
        self._check(self.n_tissue, self.n_noise)
        warm_up(self, metadata)
        return metadata

    def _check(self, n_tissue, n_noise):
        if n_tissue < 0 or n_noise < 0 or n_tissue + n_noise >= self.n_ensemble:
            raise ValueError(f"SvdClutterFilter: n_tissue ({n_tissue}) + n_noise ({n_noise}) should be "
                             f"non-negative and less than the ensemble size ({self.n_ensemble})")

    def process(self, data):
        n = data.shape[0]
        s = cp.ascontiguousarray(data).reshape(n, -1).astype(cp.complex64, copy=False)
        correlation = s @ s.conj().T                        # (n, n), Hermitian
        _, vectors = cp.linalg.eigh(correlation)            # ascending eigenvalues
        # Descending order (the strongest first); keep [n_tissue, n - n_noise).
        vectors = vectors[:, ::-1]
        kept = vectors[:, self.n_tissue:n - self.n_noise]
        filtered = (kept @ kept.conj().T) @ s               # (n, x*z)
        return filtered.reshape(data.shape)

    def get_parameters(self) -> Dict[str, ParameterDef]:
        high = (self.n_ensemble - 1) if self.n_ensemble else 31
        return {
            name: ParameterDef(
                name=name,
                space=Box(shape=(1,), dtype=np.int32, low=0, high=high),
            )
            for name in ("n_tissue", "n_noise")
        }

    def set_parameter(self, key: str, value: Sequence[Number]):
        if key not in ("n_tissue", "n_noise"):
            raise ValueError(f"{type(self).__name__} has no {key} parameter.")
        value = int(round(float(_scalar(value))))
        n_tissue = value if key == "n_tissue" else self.n_tissue
        n_noise = value if key == "n_noise" else self.n_noise
        if self.n_ensemble is not None:
            self._check(n_tissue, n_noise)
        setattr(self, key, value)

    def get_parameter(self, key: str) -> Sequence[Number]:
        if key not in ("n_tissue", "n_noise"):
            raise ValueError(f"{type(self).__name__} has no {key} parameter.")
        return getattr(self, key)
