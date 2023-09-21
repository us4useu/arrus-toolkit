.. _arrus-toolkit-examples:

========
Examples
========


Linear array
============

Raw B-mode image
----------------

GUI4us examples for the raw B-mode image presentation are available in the
``examples/linear_array/bmode``.

Please refer to ARRUS Toolkit documentation for more information about
how to configure TX/RX sequences and data processing.

Simple color/power doppler
--------------------------

GUI4us examples for the color/power doppler presentation are available in the
``examples/linear_array/color_doppler``.

There are parameter controls on the left and 2 subfigures on the right visible in the GUI.
Color and power doppler are displayed on the left and right subfigure respectively.
The following parameters are available for modification in the GUI, for both subfigures separately:

- color_dr_max: maximal value of color doppler displayed [radians], 
- color_dr_min; minimal value of color doppler displayed [radians], 
- power_dr_max: maximal value of power doppler displayed [dB], 
- power_dr_min; minimal value of power doppler displayed [dB], 

Additionally the transmit pulse amplitude can be set in 'voltage' control,
and time gain compensation (TGC) can be modified manually.

Please refer to ARRUS Toolkit documentation for more information about
how to configure TX/RX sequences and data processing.

Requirements:

- The Python packages specified in the ``examples/linear_array/color_doppler/requirements.txt`` file.

NN-B-mode
---------

GUI4us example for B-mode image reconstruction and neural network post-processing
is available in the ``examples/linear_array/nn_bmode`` directory.

The example uses the model presented in the paper
`Beamforming and Speckle Reduction Using Neural Networks, Dongwoon Hyun et. al. 2019 <https://doi.org/10.1109%2FTUFFC.2019.2903795>`__.
The source code, datasets and model weights are available in Dongwoon's git
`repository <https://gitlab.com/dongwoon.hyun/nn_bmode>`__.

In short, the

Requirements:

- The Python packages specified in the ``nn_bmode/requirements.txt`` file, 
  you can install it with the following command: ``pip install -r nn_bmode/requirements.txt``.
- NVIDIA CUDA Toolkit 11.2 (required by ``tensorflow-gpu``).
