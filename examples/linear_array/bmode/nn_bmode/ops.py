import numpy as np
import tensorflow as tf
import cupy as cp
from arrus.utils.imaging import Operation
from nn_bmode.model import NNBmode


class ApplyNNBmode(Operation):
    """
    Applies NN-Bmode model on the input LRIs.

    See the README.md for more details.
    Note: this class is not thread-safe.
    """
        
    def __init__(self, model_weights, use_dlpack=True):
        self.model_weights = model_weights
        self.model = None
        self.use_dlpack = use_dlpack
        self.n_groups = None
        self.n_tx = None
        self.nx = None
        self.nz = None
        self.group_size = None
        self.lri_grouped = None
        self.model_input = None

    def prepare(self, const_metadata):
        # Here is all
        n_seq, self.n_tx, self.nx, self.nz = const_metadata.input_shape
        n_frames = ((self.n_tx - 1) // 16 + 1) * 16
        self.n_groups = 16
        self.group_size = n_frames // self.n_groups
        self.lri_grouped = cp.zeros(
            (n_frames, self.nx, self.nz), dtype=cp.complex64)
        self.model_input = cp.zeros(
            (1, self.n_groups, 2, self.nx, self.nz), dtype=cp.float32)
        model_input_shape = (32, self.nx, self.nz)
        self.model = NNBmode(
            model_input_shape, model_weights=self.model_weights)
        # Returns output
        return const_metadata.copy(
            input_shape=(self.nz, self.nx),
            is_iq_data=False,
            dtype=np.float32
        )

    def process(self, data):
        # -- Group
        self.lri_grouped[:self.n_tx, :, :] = cp.squeeze(data)
        lri_grouped = self.lri_grouped.reshape(
            self.n_groups, self.group_size, self.nx, self.nz)
        lri_grouped = cp.sum(lri_grouped, axis=1)
        
        # -- Split I/Q to channels
        self.model_input[0, :, 0, :, :] = cp.real(lri_grouped)
        self.model_input[0, :, 1, :, :] = cp.imag(lri_grouped)
        model_input = self.model_input.reshape(
            1, self.n_groups * 2, self.nx, self.nz)

        if self.use_dlpack:
            model_input = model_input.toDlpack()
            model_input = tf.experimental.dlpack.from_dlpack(model_input)
        else:
            model_input = model_input.get()
        # Inference.
        pred = self.model.predict(model_input)
        pred = cp.asarray(pred)
        pred = cp.squeeze(pred)
        pred = 20*cp.log10(pred).T
        return pred

