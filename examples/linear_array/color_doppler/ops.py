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
        return metadata

    def process(self, data):
        output = cupyx.scipy.ndimage.convolve1d(data, self.ba[0], axis=0)
        output = output.astype("complex64")
        return output
