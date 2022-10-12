import numpy as np
import pickle
import dataclasses
import matplotlib.pyplot as plt

from parameters import *

data = pickle.load(open("data.pkl", "rb"))
rf = data["rf"]
metadata = data["metadata"]

data_desc = dataclasses.replace(metadata.data_description, sampling_frequency=65e6)

metadata = metadata.copy(input_shape=rf.shape[1:], data_desc=data_desc)

x_grid = np.arange(-10, 10, 0.1)*1e-3  # [m]
y_grid = np.arange(-8, 8, 0.1)*1e-3  # [m]
z_grid = np.arange(0, 40, 0.1)*1e-3  # [m]

downsampling_factor = 1

taps = scipy.signal.firwin(
    numtaps=128,
    cutoff=[0.7*center_frequency, 1.3*center_frequency],
    pass_zero=False,
    fs=65e6/downsampling_factor
)

rca_rec = ReconstructRCA(output_grid=(x_grid, y_grid, z_grid),
                         angles=angles,
                         speed_of_sound=c)

pipeline = Pipeline(
    steps=(
        RemapToLogicalOrder(),
        Transpose(axes=(0, 1, 3, 2)),
        FirFilter(taps),
        QuadratureDemodulation(),
        Decimation(decimation_factor=4, cic_order=2),
        rca_rec,
        # Mean(axis=0),
        # EnvelopeDetection(),
        # # Envelope compounding
        # Transpose((2, 0, 1)),  # (y, x, z) -> (z, y, x)
        # Lambda(lambda data: data/cp.nanmax(data)),
        # LogCompression(),
        # Lambda(
        #     lambda data: data[:, len(y_grid)//2, :],
        #     lambda metadata: metadata.copy(input_shape=(len(z_grid), len(x_grid))))
    ),
    placement="/GPU:0"
)

pipeline.prepare(metadata)
result = pipeline.process(cp.asarray(rf[0]))[0].get()

print(result.shape)

plt.imshow(20*np.log10(np.abs(result[8, :, 80])), vmin=20, vmax=100)
# plt.imshow(result[0, 8], vmin=-100, vmax=100)
plt.show()

plt.show()


# Czy opoznienie nadawcze + odbiorcze dla glebokosci 20 mm (odpowiedni piksel) jest ok?
print(rca_rec.tx_delays.shape)
print(rca_rec.rx_delays.shape)
print(rca_rec.tx_delays[8, 40, 200]*65e6)


plt.plot(rca_rec.rx_delays.get()[:, 100, 200]*65e6)
plt.show()


# plt.imshow(np.real(result[8, 40]))
# plt.show()
