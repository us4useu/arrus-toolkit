"""Utility functions used in example notebooks"""

import tensorflow as tf
import cupy as cp
import numpy as np
import matplotlib.pyplot as plt
from matplotlib import animation
from arrus.utils.imaging import Operation


# Helper functions
def show_cineloop(imgs, value_range=None, cmap=None, figsize=None, 
                  interval=50, xlabel="Azimuth (mm)", ylabel="Depth (mm)",
                  extent=None):
    
    def init():
        img.set_data(imgs[0])
        return (img,)

    def animate(frame):
        img.set_data(imgs[frame])
        return (img,)

    fig, ax = plt.subplots()
    if figsize is not None:
        fig.set_size_inches(figsize)
    img = ax.imshow(imgs[0], cmap=cmap, extent=extent)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    if value_range is not None:
        img.set_clim(*value_range)
        
    return animation.FuncAnimation(
        fig, animate, init_func=init, frames=len(imgs), 
        interval=interval, blit=True)


class Reshape(Operation):

    def __init__(self, shape):
        self.shape = shape

    def _prepare(self, const_metadata):
        return const_metadata.copy(input_shape=self.shape)

    def _process(self, data):
        return data.reshape(self.shape)


class RunForDlPackCapsule(Operation):
    """Note: experimental"""

    def __init__(self, callback):
        """
        @param callback - a callback to call for new DL pack capsue
        """
        self.callback = callback

    def _prepare(self, const_metadata):
        return const_metadata

    def _process(self, data):
        dlpack_capsule = data.toDlpack()
        return self.callback(dlpack_capsule)
