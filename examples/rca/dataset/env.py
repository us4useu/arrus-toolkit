import queue
from typing import Sequence, Callable, Iterable

import numpy as np
from arrus.utils.imaging import *

from gui4us.common import ImageMetadata
from gui4us.model import *
from gui4us.logging import get_logger
import threading
import cupy as cp
import scipy.signal
import numpy as np

import arrus.medium

import probe_params
from dataset import load_rca_cyst_dataset
from sequence import create_sequence
from reconstruction import get_pwi_reconstruction


class OfflineProcessingStream(Stream):

    def __init__(self, data_queue, queue_timeout=20):
        self.data_queue = data_queue
        self.thread = threading.Thread(target=self._produce_data)
        self.queue_timeout = queue_timeout
        self._is_running = False
        self.callbacks = []

    def append_on_new_data_callback(self, callback: Callable):
        self.callbacks.append(callback)

    def stop(self):
        self._is_running = False
        self.thread.join()

    def start(self):
        self._is_running = True
        self.thread.start()

    def _produce_data(self):
        while self._is_running:
            arrays = self.data_queue.get(timeout=20)
            for c in self.callbacks:
                c(arrays)


class OfflineDataEnv(Env):
    def __init__(self, input_data, input_metadata, processing):
        self.logger = get_logger(type(self))
        self.producer = threading.Thread(target=self._process_data)
        self.nframes = cp.asarray(input_data)
        self.data_size = len(self.input_data)
        self.stream_metadata = self._initialize_processing(
            input_metadata,
            processing
        )
        self.stream = None
        self._state_lock = threading.Lock()
        self.is_running = False
        self.i = None

    def _initialize_processing(self, metadata, processing):
        output_metadata = processing.prepare(metadata)
        if not isinstance(output_metadata, Iterable):
            output_metadata = [output_metadata]

        stream_metadata_coll = {}
        for i, om in enumerate(output_metadata):
            key = StreamDataId("default", 0)
            value = ImageMetadata(
                shape=om.input_shape,
                dtype=om.input_dtype,
                ids=("OZ", "OX"), # TODO
                units=("m", "m"),  # TODO
                # extents=((0, 10), (-5, 5)) TODO
            )
            stream_metadata_coll[key] = value
        return MetadataCollection(stream_metadata_coll)

    def _process_data(self):
        while self.is_running:
            output = self.processing.process(self.input_data[self.i % self.nframes])
            arrs = [o.get() for o in output]
            self.queue.put(arrs)

    def get_settings(self) -> Sequence[SettingDef]:
        return [
            SettingDef(
                name="Voltage",
                space=Box(
                    shape=(1,),
                    dtype=np.float32,
                    low=0,
                    high=100
                ),
                initial_value=10,
                step=5
            ),
            SettingDef(
                name="TGC",
                space=Box(
                    shape=(10,),
                    dtype=np.float32,
                    low=14,
                    high=54,
                    name=[f"{i} [mm]" for i in range(10)],
                    unit=["dB"]*10
                ),
                initial_value=[20]*10,
                step=1
            ),
        ]

    def start(self) -> None:
        with self._state_lock:
            self.i = 0
            self.queue = queue.Queue(maxsize=2)
            self.stream = OfflineProcessingStream(self.queue)
            self.is_running = True
            self.producer.start()
            self.stream.start()

    def stop(self) -> None:
        with self._state_lock:
            self.stream.stop()
            self.is_running = False
            self.producer.join()

    def close(self) -> None:
        self.stop()

    def set(self, action: SetAction) -> None:
        print(f"Got action: {action}")

    def get_stream(self) -> Stream:
        return self.stream

    def get_stream_metadata(self) -> MetadataCollection:
        return self.stream_metadata


rf, metadata = load_rca_cyst_dataset()
MEDIUM = arrus.medium.Medium(name="tissue", speed_of_sound=1540)
angles = np.linspace(-10, 10, 64) * np.pi / 180  # [rad]
center_frequency = 6e6  # [Hz]
n_periods = 2
sample_range = (0, 5 * 1024)
pri = 400e-6

# TX/RX PW sequence
sequence_xy, sequence_yx = create_sequence(
    medium=MEDIUM,
    angles=angles,
    n_periods=n_periods,
    center_frequency=center_frequency,
    sample_range=sample_range,
    pri=pri
)

    # Image reconstruction.
fir_taps = scipy.signal.firwin(
    numtaps=64, cutoff=np.array([0.5, 1.5]) * center_frequency,
    pass_zero="bandpass", fs=metadata.context.device.sampling_frequency
)

pipeline = get_pwi_reconstruction(
    array_x=probe_params.APERTURE_X,
    array_y=probe_params.APERTURE_Y,
    y_grid=np.arange(-6e-3, 6e-3, 0.4e-3),
    x_grid=np.arange(-6e-3, 6e-3, 0.4e-3),
    z_grid=np.arange(25e-3, 43e-3, 0.4e-3),
    fir_taps=fir_taps,
    sequence_xy=sequence_xy,
    sequence_yx=sequence_yx,
    dr_min=-5, dr_max=120,
)

ENV = OfflineDataEnv(input_data=rf, input_metadata=metadata, processing=pipeline)
