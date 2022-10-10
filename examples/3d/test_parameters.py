import parameters
import numpy as np
import matplotlib.pyplot as plt

delays = parameters.get_delays_raw([-10e-3], [-10 * np.pi / 180], [-10 * np.pi / 180], 0.5e-3, 1024, 1450)
delays = delays.reshape((-1, 32, 32))
plt.imshow(delays[0])
plt.colorbar()
plt.show()
