# RF Data Acquisition Scripts for us4R-lite with RCA Probe

This repository contains a set of scripts for collecting large RF channel data sets from the **us4R-lite ultrasound system** with a **Vermon RCA 64+64 probe**. 

The scripts are designed to acquire long RF frame sequences **without on-the-fly processing**, so that the **maximum frame rate is not limited by computation**. 
The frame rate is constrained only by:
- ultrasound wave propagation time, 
- data transfer time, 
- memory copy time from device to host PC. 

---

## Requirements

- **ARRUS 0.10.x** (recommended: `0.10.6`) 
- **us4R-lite+** with **Vermon RCA 64+64 probe**

---

## Installation

1. Clone this repository or download this folder. 
2. Install additional Python tools (required for the `display.ipynb` notebook): 
   ```bash
   pip install git+https://github.com/pjarosik/pjtools
   ```

---

## Usage

### 1. Acquiring RF Data

To perform a measurement, use **`acquire_offline.py`**:

1. Adjust acquisition parameters defined in the `__main__` section of the script (e.g., pulse length, voltage, etc.). In particular:
  a. `batch_size`, i.e. the number of frames that will be acquired by the ultrasound into device's internal DDR memory, before sending it to the host PC RAM memory. Increasing this value increases the amount of data transferred from the system to the computer, thereby reducing the impact of transfer overhead on the frame rate.
  b. `nframes`: total number of frames to acquire. NOTE: this must be a multiple of the `batch_size`.
  c. `pri`: pulse repetition interval, i.e. the time interval between two physical TX/RX events. NOTE: for the us4R-lite+ and the Vermon RCA 64+64 probe, each full RX aperture acquisition requires two TX/RXs (due to the quite unusual pin-mapping between the probe and the ultrasound system). 
  d. Try reducing the `pri` until you encounter buffer overflow errors.
2. Run the script: 
   ```bash
   python acquire_offline.py dataset_name
   ```
   
   
NOTE: if you get buffer overflow erros, e.g.:
```
WARNING: Detected RX data overflow.
```

it is likely that the data transfer from the ultrasound system to the host PC cannot keep up with the ultrasound frame acquisition rate (according to the given PRI). To mitigate this issue, consider increasing the PRI or enlarging the batch size.

If successful, the script will generate: 
- `dataset_name_raw_dataset.pkl` 
  - contains physically raw RF data (i.e. exactly in the format as produced by the ultrasound system), 
  - metadata including frame rate (`time_intervals` – the time between consecutive batches), 
  - see the `__main__` section in `acquire_offline.py` for the full list of stored information. 

---

### 2. Reconstructing High-Resolution Images

To process the acquired dataset into a **High-Resolution Image (HRI)**, use **`reconstruct.py`**:

1. Adjust reconstruction parameters in the `__main__` section of the script (e.g., grid resolution). 
   > Note: here you are not limited by real-time constraints – feel free to use optimal parameters. 
2. Run the script: 
   ```bash
   python reconstruct.py dataset_name
   ```

If successful, the script will generate: 
- `dataset_name_beamformed_dataset.h5` 
  - contains RF data in logical order (frame number, transmit number, sample count, receive channels), 
  - high-resolution images.

---

### 3. Visualizing RF and HRI Data

You can visualize both raw RF data and the reconstructed HRI using the Jupyter notebook: 
- **`display.ipynb`**

---