import numpy as np
import tensorflow as tf
import cupy as cp
from arrus.utils.imaging import Operation
from nn_bmode import NNBmode


class ApplyNNBmode(Operation):
    """Note: this class is not thread-safe."""
        
    def __init__(self, model_weights, use_dlpack=True):
        self.model_weights = model_weights
        self.model = None
        self.use_dlpack = use_dlpack
    
    def _prepare(self, const_metadata):
        
        # Here is all 
        self.n_transmits, self.x_size, self.z_size = const_metadata.input_shape
        n_frames = ((self.n_transmits-1)//16+1)*16
        self.n_groups = 16
        self.group_size = n_frames//self.n_groups
        self.lri_grouped = cp.zeros((n_frames, self.x_size, self.z_size), dtype=cp.complex64)
        self.model_input = cp.zeros((1, self.n_groups, 2, self.x_size, self.z_size), dtype=cp.float32) 
        model_input_shape = (32, self.x_size, self.z_size)
        self.model = NNBmode(model_input_shape, model_weights=self.model_weights)
        return const_metadata.copy(input_shape=(self.z_size, self.x_size), is_iq_data=True, dtype=np.float32) 

    def _process(self, data):
        # Preprocessing. (NOTE: still requires some optimization)
        # -- Group
        self.lri_grouped[:self.n_transmits, :, :] = data
        lri_grouped = self.lri_grouped.reshape(self.n_groups, self.group_size, self.x_size, self.z_size)
        lri_grouped = cp.sum(lri_grouped, axis=1)
        
        # -- Split I/Q to channels
        self.model_input[0, :, 0, :, :] = cp.real(lri_grouped)
        self.model_input[0, :, 1, :, :] = cp.imag(lri_grouped)
        model_input = self.model_input.reshape(1, self.n_groups*2, self.x_size, self.z_size)

        if self.use_dlpack:
            model_input = model_input.toDlpack()
            model_input = tf.experimental.dlpack.from_dlpack(model_input)
        else:
            model_input = model_input.get()
        
        # Inference.
        pred = self.model.predict(model_input)
        # postprocessing (NOTE: done by CPU, but it is relatively cheap)
        pred = np.squeeze(pred)
        pred = 20*np.log10(pred).T
        return pred

