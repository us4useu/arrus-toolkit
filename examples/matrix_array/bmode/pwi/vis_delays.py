import numpy as np
import matplotlib.pyplot as plt
from imaging import get_delays_raw

angles_oxz = np.linspace(-10, 10, 7) * np.pi / 180  # [rad]
angles_oyz = np.zeros(len(angles_oxz))
tx_focus = np.zeros(len(angles_oxz))
tx_focus[:] = np.inf

delays = get_delays_raw(tx_focus, angles_oxz, angles_oyz, pitch=0.3e-3, speed_of_sound=1450, n_elements=1024)
delays = delays.reshape(-1, 32, 32)
print(delays[:, 15, 15])
for d in delays:
    plt.imshow(d.reshape(32, 32))
    plt.show()
