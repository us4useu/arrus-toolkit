import cupyx.scipy.ndimage
import numpy as np
import cupy as cp
import pickle
import dataclasses
import matplotlib.pyplot as plt
import scipy.signal
from parameters_rf import *
from tools import hilbert, Hilbert
from arrus.utils.imaging import *


data = pickle.load(open("data.pkl", "rb"))
rf = data["rf"]
metadata = data["metadata"]

data_desc = dataclasses.replace(metadata.data_description, sampling_frequency=65e6)

metadata = metadata.copy(input_shape=rf.shape[1:], data_desc=data_desc)

x_grid = np.arange(-10, 10, 0.1)*1e-3  # [m]
y_grid = np.arange(-10, 10, 0.1)*1e-3  # [m]
z_grid = np.arange(5, 55, 0.1)*1e-3  # [m]

downsampling_factor = 1

taps = scipy.signal.firwin(
    numtaps=64,
    cutoff=[0.5*center_frequency, 1.5*center_frequency],
    pass_zero=False,
    fs=65e6/downsampling_factor
)

rca_rec = ReconstructRCA(output_grid=(x_grid, y_grid, z_grid),
                         angles=angles,
                         speed_of_sound=c)

gain = cp.arange(0, 4096).reshape((1, 1, 1, 4096))
gain2 = cp.arange(0, 500).reshape((1, 1, 500))
gain2 = cp.repeat(gain2, repeats=200, axis=0)
gain2[:100, 0, :] = 2*gain2[0, :100, :] 
gain2[:, :, 320:] = 2*gain2[:, :, 320:] 


pipeline = Pipeline(
    steps=(
        RemapToLogicalOrder(),
        Transpose(axes=(0, 1, 3, 2)),
        # Lambda(lambda data: data*gain),
        Lambda(lambda data: data, prepare_func=lambda metadata: (print(metadata.input_shape), metadata)[1]),
        FirFilter(taps),
        rca_rec,
        Mean(axis=0),
        Hilbert(),
        EnvelopeDetection(),
        Squeeze(),
        Lambda(lambda data: data*gain2),
        Lambda(lambda data: data/cp.nanmax(data)),
        LogCompression(),
        # # Envelope compounding
        Transpose((2, 0, 1)),  # (y, x, z) -> (z, y, x)
        # LogCompression(),
        #Lambda(
        #    lambda data: data[:-100, len(y_grid)//2+34, :],
        #    lambda metadata: metadata.copy(input_shape=(len(z_grid)-100, len(x_grid)))),
        Lambda(
            lambda data: data[:-100, :, len(x_grid)//2],
            lambda metadata: metadata.copy(input_shape=(len(z_grid)-100, len(y_grid)))),

        # Lambda(
        #    lambda data: cp.concatenate((
        #         data[:-100, len(y_grid)//2+34, :],
        #        data[:-100, :, len(x_grid)//2]), axis=1),
        #    lambda metadata: metadata.copy(input_shape=(len(z_grid)-100, len(x_grid)+len(y_grid)))),

        Lambda(lambda data: cupyx.scipy.ndimage.median_filter(data, size=5))
    ),
    placement="/GPU:0"
)

pipeline.prepare(metadata)
result = pipeline.process(cp.asarray(rf[0]))[0].get()

# result = result.transpose((0, 2, 1, 3))
# hri = np.mean(result, axis=0)
# envelope = np.abs(scipy.signal.hilbert(hri))
# envelope = envelope/np.nanmax(envelope)
# result = 20*np.log10(envelope)
# plt.imshow(result[:, 115], cmap="gray", vmin=-80, vmax=0)
plt.imshow(result, cmap="gray", vmin=-30, vmax=0, extent=[-10, 10, 45, 5])
plt.xlabel("OX (mm)")
plt.ylabel("OZ (mm)")
plt.colorbar()
# # plt.imshow(result[:, ]
plt.show()
