from datetime import datetime
import pickle
import matplotlib.pyplot as plt
import arrus
import arrus.medium
import arrus.ops.tgc
import arrus.session
import arrus.utils.imaging
import arrus.utils.us4r
import numpy as np
import sys
from arrus.ops.us4r import (
    Scheme, TxRxSequence, TxRx, Tx, Rx, Aperture, Pulse
)
from arrus.utils.gui import (
    Display2D, View2D, Layer2D
)
import scipy.signal
from arrus.utils.imaging import *
import hyperion_ultrasound.imaging.parameters as params
from reconstruction import *
from hyperion_ultrasound.imaging.sequence2 import *
from pjtools.visualization import create_animation
import time

import arrus
from arrus.ops.us4r import Scheme, Pulse, DataBufferSpec
from arrus.ops.imaging import PwiSequence
from arrus.utils.gui import Display2D, View2D, Layer2D
from arrus.utils.imaging import *

with arrus.Session("us4r.prototxt") as sess:
    us4r = sess.get_device("/Us4R:0")
    us4r.set_hv_voltage(10)

    MEDIUM = arrus.medium.Medium(name="tissue", speed_of_sound=1450)
    background_angles = np.linspace(-10, 10, 32) * np.pi / 180  # [rad]
    center_frequency = 6e6  # [Hz]
    n_periods = 2
    sample_range = (0, 4*1024)
    pri = 400e-6

    # BACKGROUND B-MODE IMAGE SEQUENCES.
    sequence_xy = TxRxSequence(
        ops=[
                TxRx(
                    tx=Tx(
                        # Transmit with all elements.
                        # Center == 0.0 means the center of the probe.
                        aperture=Aperture(center=0.0,
                                          size=params.aperture_x.n_elements),
                        excitation=Pulse(
                            center_frequency=center_frequency,
                            n_periods=n_periods,
                            inverse=False
                        ),
                        # Plane wave (focus depth = inf).
                        focus=np.inf,
                        angle=angle,
                        speed_of_sound=MEDIUM.speed_of_sound
                    ),
                    rx=Rx(
                        # Receive with all elements.
                        aperture=Aperture(center=0.0,
                                          size=params.aperture_y.n_elements),
                        sample_range=sample_range,
                    ),
                    pri=pri
                )
                for angle in background_angles
            ],
            tgc_curve=[]  # Will be set later.
        )

    # Transmit with OY elements, receive with OX elements.
    sequence_yx = TxRxSequence(
            ops=[
                TxRx(
                    tx=Tx(
                        # Transmit with all elements.
                        # Center == 0.0 means the center of the probe.
                        aperture=Aperture(
                            center=0.0,
                            size=params.aperture_y.n_elements),
                        excitation=Pulse(
                            center_frequency=center_frequency,
                            n_periods=n_periods,
                            inverse=False
                        ),
                        # Plane wave (focus depth = inf).
                        focus=np.inf,
                        angle=angle,
                        speed_of_sound=MEDIUM.speed_of_sound
                    ),
                    rx=Rx(
                        # Receive with all elements.
                        aperture=Aperture(
                            center=0.0,
                            size=params.aperture_x.n_elements),
                        sample_range=sample_range,
                    ),
                    pri=pri
                )
                for angle in background_angles
            ],
            tgc_curve=[]  # Will be set later.
        )

    sequence = get_system_sequence_for_descriptions(
            sequence_descriptions=[
                SequenceDescription(
                    sequence=sequence_xy,
                    probe_tx=params.aperture_x,
                    probe_rx=params.aperture_y,
                    orientation="ortho"
                ),
                SequenceDescription(
                    sequence=sequence_yx,
                    probe_tx=params.aperture_y,
                    probe_rx=params.aperture_x,
                    orientation="ortho"
                ),
            ],
            system_interface=us4r.get_probe_model()
        )

    fir_taps = scipy.signal.firwin(
            numtaps=64, cutoff=np.array([0.5, 1.5]) * center_frequency,
            pass_zero="bandpass", fs=65e6)

    pipeline = get_pwi(
        probe_x=params.aperture_x,
        probe_y=params.aperture_y,
        y_grid=np.arange(-8e-3, 8e-3, 0.2e-3),
        x_grid=np.arange(-8e-3, 8e-3, 0.2e-3),
        z_grid=np.arange(5e-3, 45e-3, 0.2e-3),
        fir_taps=fir_taps,
        sequence_xy=sequence_xy,
        sequence_yx=sequence_yx,
        dr_min=30, dr_max=80,
        frame_decimation=2
    )
    pipeline_wrapper = Pipeline(
        steps=(
            Lambda(
                lambda data: pipeline.process(data)[0],
                prepare_func=lambda metadata: pipeline.prepare(metadata)[0]
            ),
        ),
        placement="/GPU:0"
    )
    scheme = Scheme(
        tx_rx_sequence=sequence,
        processing=pipeline_wrapper
    )
    buffer, metadata = sess.upload(scheme)
    us4r.set_tgc([50]*26)
    sess.start_scheme()
    from visualizer import VTKVisualizer
    visualizer = VTKVisualizer(metadata.input_shape)
    while True:
        arr = buffer.get()[0]
        visualizer.update(arr)
