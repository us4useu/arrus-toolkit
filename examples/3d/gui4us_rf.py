from gui4us.cfg.environment import *
from gui4us.cfg.display import *
from arrus.utils.imaging import *
from arrus.devices.probe import ProbeModel, ProbeModelId
import importlib
import sys
import gui4us.cfg


# Utility functions
def load_module(module_name, path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


CWD = os.path.dirname(__file__)

parameters = load_module("parameters", os.path.join(CWD, "parameters.py"))
from parameters import *


# Processing parameters
tx_focus = [np.inf]  # [m]
tx_ang_zx = [0]  # [rad]
tx_ang_zy = [0]  # [rad]

# TODO the below is duplicated from dictionary.prototxt
# TODO make it possible to specify tx_rx_sequence and pipeline as a function
#  of session (session_context) and imaging_context (with metadata)
probe_model = ProbeModel(
    ProbeModelId("vermon", "15"),
    n_elements=1024,
    pitch=0.5e-3,
    curvature_radius=np.nan)

sequence = get_sequence(probe_model,
                        tx_focus=tx_focus,
                        tx_ang_zx=tx_ang_zx,
                        tx_ang_zy=tx_ang_zy)
imaging, data_queue = get_imaging(sequence=sequence,
                                  tx_focus=tx_focus,
                                  tx_ang_zx=tx_ang_zx,
                                  tx_ang_zy=tx_ang_zy)

# imaging, data_queue = get_rf_imaging(sequence=sequence)

medium = arrus.medium.Medium(
    name="ATS560H",
    speed_of_sound=1450  # [m/s]
)

environment = HardwareEnvironment(
    session_cfg=os.path.join(CWD, "us4r.prototxt"),
    tx_rx_sequence=sequence,
    pipeline=imaging,
    work_mode="HOST",
    capture_buffer_capacity=5,
    tx_voltage=tx_voltage,
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
    "rf": Display2D(
        title=f"",
        layers=(
            Layer2D(
                value_range=(-1000, 1000),
                cmap="viridis",
                input=LiveDataId("default", 1),
                extent=((0, n_samples//10), (0, 1024))
            ),
        )
    )
}


view_cfg = ViewCfg(displays)
