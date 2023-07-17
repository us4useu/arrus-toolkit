.. _arrus-toolkit-installation:

============
Installation
============

Configurations
==============

Installing each component separately
====================================

Firmware
--------

Note: currently the firmware update is supported on Windows only.


Drivers
-------

Linux
~~~~~

Windows
~~~~~~~

Make sure that your us4R-lite device is properly connected via a Thunderbolt-3
cable and is enabled in your Thunderbolt software, e.g.:

.. figure:: img/thunderbolt.png
    :scale: 80%

The `Connection status` should read `Connected` (or something similar).

Uninstall ARIUS drivers (if previously installed)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
If ARIUS drivers are installed on your computer, uninstall them first. ARIUS
drivers are the legacy drivers that were required before the 0.4.3 version.

1. Open the Windows Device Manager, uninstall all ``ARIUS`` or ``WinDriver1290`` or ``us4oem``
   devices available in the "Jungo Connectivity" node. **Check
   "Delete the driver software for this device"**.

.. figure:: img/uninstall_arius_drv.png
    :scale: 100%

2. Restart computer.


Install Us4OEM drivers
~~~~~~~~~~~~~~~~~~~~~~

1. Download and extract ``us4oem-drivers-1450.zip`` (contact us4us support to make sure you get the newest version).
2. Run ``install.bat`` with **administrative privileges**. Confirm driver
   installation if necessary.

``us4oem`` and ``WinDriver1450`` nodes should now be visible in the
Device Manager.

.. figure:: img/dev_manager.png
    :scale: 100%


ARRUS
-----

Before you proceed, please make sure the device is properly connected to the computer.

The list of the ARRUS package release is available `here <https://github.com/us4useu/arrus/releases>`__.

Download and extract the package for the programming language you want to use. Follow the necessary
steps below.

MATLAB
~~~~~~

Requirements:

- MATLAB 2019a,
- NVIDIA CUDA Toolkit 10.0.

Add ``arrus`` subdirectory to the MATLAB search paths.

To check if everything is ok run script
``examples\Us4R_control.m`` (for Esaote adapter) or
``examples\Us4RUltrasonix_control.m`` (for Ultrasonix adapter).


Python
~~~~~~

Requirements:

- Python 3.8 (or newer, for ARRUS >= 0.9.0),
- NVIDIA CUDA Toolkit >= 10.0.

We recommend using `Miniconda3 <https://docs.conda.io/en/latest/miniconda.html>`__
to manage Python environments.

Install whl package located in the ``python`` subdirectory using
``pip`` package manager:

.. code-block:: console

    pip uninstall arrus
    pip install  arrus-x.y.z-cp38-cp38-win_amd64.whl

Where ``x.y.z`` is the current version of ARRUS package.

The below packages are required to run the example with B-mode imaging:

.. code-block:: console

    pip install cupy-cudaxyz matplotlib

Where ``xyz`` is the version of the CUDA Toolkit installed on your host PC.

To check if everything is ok, run one of the scripts available
`here <https://github.com/us4useu/arrus/tree/master/api/python/examples>`__.




