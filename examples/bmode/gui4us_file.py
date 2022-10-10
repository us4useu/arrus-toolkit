import os.path
import cupy as cp

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
import pickle

CWD = os.path.dirname(__file__)

x_grid = np.arange(-15, 15, 0.1) * 1e-3
z_grid = np.arange(5, 40, 0.1) * 1e-3

medium = arrus.medium.Medium(
    name="ATS560H",
    speed_of_sound=1450  # [m/s]
)

dataset = pickle.load(open("/home/pjarosik/Downloads/d1rs.pkl", "rb"))

environment = DatasetEnvironment(
    input=dataset,
    pipeline= Pipeline(
        steps=(
            # Channel data pre-processing.
            RemapToLogicalOrder(),
            Transpose(axes=(0, 1, 3, 2)),
            BandpassFilter(),
            QuadratureDemodulation(),
            Decimation(decimation_factor=4, cic_order=2),
            # Data beamforming.
            ReconstructLri(x_grid=x_grid, z_grid=z_grid),
            # IQ compounding
            Mean(axis=1),  # Along tx axis.
            # Post-processing to B-mode image.
            # Lambda(lambda data: data/cp.max(data)),
            EnvelopeDetection(),
            # Envelope compounding
            Mean(axis=0),
            Transpose(),
            Lambda(lambda data: data / cp.nanmax(data)),
            LogCompression()
        ),
        placement="/GPU:0"),
    input_nr=1,
    medium=medium
)

displays = {
    "bmode": Display2D(
        title=f"B-mode",
        layers=(
            Layer2D(
                value_range=(-40, 0),
                cmap="gray",
                input=LiveDataId("default", 0)
            ),
        )
    )
}

view_cfg = ViewCfg(displays)
