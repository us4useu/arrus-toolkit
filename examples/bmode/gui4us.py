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

CWD = os.path.dirname(__file__)

medium = arrus.medium.Medium(
    name="ATS560H",
    speed_of_sound=1450  # [m/s]
)

x_grid = np.arange(-15, 15, 0.1) * 1e-3
z_grid = np.arange(5, 40, 0.05) * 1e-3

environment = HardwareEnvironment(
    session_cfg=os.path.join(CWD, "us4r.prototxt"),
    tx_rx_sequence=PwiSequence(
        angles=np.linspace(-10, 10, 7) * np.pi / 180,
        pulse=Pulse(center_frequency=6e6, n_periods=2, inverse=False),
        rx_sample_range=(0, 1024*4),
        downsampling_factor=1,
        speed_of_sound=medium.speed_of_sound,
        pri=200e-6),
    pipeline=Pipeline(
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
    work_mode="HOST",
    capture_buffer_capacity=20,
    tx_voltage=5,
    tx_voltage_step=5,
    rx_buffer_size=4,
    host_buffer_size=4,
    medium=medium,
    tgc_curve=gui4us.cfg.LinearFunction(
        intercept=14,  # [dB]
        slope=2e2
    )
)

displays = {
    "bmode": Display2D(
        title=f"B-mode",
        layers=(
            Layer2D(
                value_range=(-60, 0),
                cmap="gray",
                input=LiveDataId("default", 0)
            ),
        )
    )
}

view_cfg = ViewCfg(displays)
