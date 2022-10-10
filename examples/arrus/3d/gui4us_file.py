import os.path

import arrus.medium
from gui4us.cfg.environment import *
from gui4us.cfg.display import *
from arrus.ops.us4r import *
from arrus.utils.imaging import *
from arrus.ops.imaging import *
from arrus.devices.probe import ProbeModel, ProbeModelId
from arrus.medium import Medium
import numpy as np
import scipy.signal
import importlib
import sys
import gui4us.cfg
import cupy as cp
from gui4us.model.dataset import *
import pickle
from arrus.utils.imaging import *

speed_of_sound = 1450
tx_frequency = 3.0e6
n_periods = 2
n_samples = 8192
x_grid = np.arange(-5, 5, 0.2) * 1e-3  # [m]
y_grid = np.arange(-5, 5, 0.2) * 1e-3  # [m]
z_grid = np.arange(0, 60, 0.2) * 1e-3  # [m]

probe_model = ProbeModel(
    ProbeModelId("vermon", "15"),
    n_elements=1024,
    pitch=0.5e-3,
    curvature_radius=np.nan)


class Slice(Operation):

    def __init__(self, axis, position):
        self.axis = axis
        self.position = position

    def prepare(self, const_metadata):
        self.input_shape = const_metadata.input_shape
        axis_length = self.input_shape[self.axis]
        self.actual_position = axis_length//2 + self.position
        self.slices = [slice(0, None)]*len(self.input_shape)
        self.slices[self.axis] = self.actual_position
        self.slices = tuple(self.slices)
        print(self.slices)
        output_shape = list(self.input_shape)
        del output_shape[self.axis]
        output_shape = tuple(output_shape)

        output_grid = list(const_metadata.data_description.grid)
        del output_grid[self.axis]
        data_desc = dataclasses.replace(
            const_metadata.data_description, grid=output_grid)
        return const_metadata.copy(input_shape=output_shape,
                                   data_desc=data_desc)

    def process(self, data):
        result = data[self.slices]
        return result


def get_imaging(tx_focus, tx_ang_zx, tx_ang_zy, n_last_frames_to_save=10):

    pipeline = Pipeline(
        steps=(
            # RemapToLogicalOrder(),
            Transpose(axes=(0, 1, 3, 2)),
            Reshape(1, 1, 32, 32, n_samples),
            BandpassFilter(),
            QuadratureDemodulation(),
            Decimation(decimation_factor=30, cic_order=2),
            ReconstructLri3D(
                x_grid=x_grid, y_grid=y_grid, z_grid=z_grid,
                tx_foc=tx_focus,
                tx_ang_zx=tx_ang_zx, tx_ang_zy=tx_ang_zy,
                speed_of_sound=speed_of_sound),
            Squeeze(),
            EnvelopeDetection(),
            Lambda(lambda data: data / cp.nanmax(data)),
            LogCompression(),
            Pipeline(
                steps=(
                    Slice(axis=1, position=0),
                    Transpose()
                ),
                placement="/GPU:0"
            ),
            Slice(axis=0, position=0),
            Transpose()
        ),
        placement="/GPU:0"
    )
    return pipeline


medium = arrus.medium.Medium(
    name="ATS560H",
    speed_of_sound=1450  # [m/s]
)

dataset = pickle.load(open("/home/pjarosik/Downloads/d1rs.pkl", "rb"))

environment = DatasetEnvironment(
    input=dataset,
    pipeline=get_imaging(-30e-3, 0, 0),
    input_nr=1,
    medium=medium
)

displays = {
    "OXZ": Display2D(
        title=f"OXZ",
        layers=(
            Layer2D(
                value_range=(-80, 0),
                cmap="gray",
                input=LiveDataId("default", 0),
                ax_labels=("OZ", "OX"),
            ),
        )
    ),
    "OYZ": Display2D(
        title=f"OYZ",
        layers=(
            Layer2D(
                value_range=(-80, 0),
                cmap="gray",
                input=LiveDataId("default", 1),
                ax_labels=("OZ", "OY"),
            ),
        )
    )
}

view_cfg = ViewCfg(displays)
