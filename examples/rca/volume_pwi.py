import arrus
import arrus.session
import arrus.utils.imaging
import arrus.utils.us4r
from collections import deque
import numpy as np
import cupy as cp
import pathlib
import os.path
from arrus.utils.imaging import *
import time
import sys
import vtk
from PyQt5 import QtCore, QtWidgets
from vtk.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
import time
from tools import *
import vtk.util.numpy_support
import matplotlib.pyplot as plt
import cupyx.scipy.ndimage
from parameters_rf import *
from vtkmodules.vtkCommonColor import vtkNamedColors
from vtkmodules.vtkCommonDataModel import vtkImageData
from vtkmodules.vtkFiltersCore import (
    vtkFlyingEdges3D,
    vtkMarchingCubes
)
from vtkmodules.vtkIOImage import vtkPNGWriter
from vtkmodules.vtkRenderingCore import (
    vtkActor,
    vtkActor2D,
    vtkPolyDataMapper,
    vtkImageMapper,
    vtkRenderWindow,
    vtkRenderWindowInteractor,
    vtkRenderer,
    vtkCamera,
    vtkWindowToImageFilter
)

from arrus.ops.us4r import (
    Scheme,
    Pulse,
    Tx,
    Rx,
    TxRx,
    TxRxSequence
)
from arrus.utils.imaging import (
    Pipeline,
    SelectFrame,
    Squeeze,
    Lambda,
    RemapToLogicalOrder,
    SelectSequence
)
from arrus.utils.gui import (
    Display2D
)

arrus.set_clog_level(arrus.logging.TRACE)
arrus.add_log_file("test.log", arrus.logging.INFO)

pitch = 0.2e-3  # [m]
n_elements_single_axis = 128
total_n_elements = 2*n_elements_single_axis
c = 1450  # [m/s]
center_frequency = 6e6
tx_voltage = 45
n_samples = 4096
angles = np.linspace(-10, 10, 16)*np.pi/180

rows = np.zeros(total_n_elements).astype(bool)
columns = np.zeros(total_n_elements).astype(bool)
columns[:n_elements_single_axis] = True
rows[n_elements_single_axis:] = True


CWD = os.path.dirname(os.path.abspath(__file__))


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        self.window_closed = threading.Event()
        QtWidgets.QMainWindow.__init__(self, parent)

        self.frame = QtWidgets.QFrame()
        colors = vtkNamedColors()

        self.vl = QtWidgets.QVBoxLayout()
        self.vtkWidget = QVTKRenderWindowInteractor(self.frame)
        self.vl.addWidget(self.vtkWidget)
        self.initialize()
        shape = self.metadata.input_shape

        dimensions = tuple(reversed(shape))
        data = (np.random.rand(*shape).astype(np.float32))*20

        self.volume = vtkImageData()
        self.volume_data = vtk.util.numpy_support.numpy_to_vtk(data.ravel(), deep=True)
        self.volume.SetDimensions(*dimensions)
        self.volume.SetSpacing([1, 1, 1])
        self.volume.SetOrigin([0, 0, 0])
        self.volume.GetPointData().SetScalars(self.volume_data)
        self.surface = vtkFlyingEdges3D()
        self.surface.SetInputData(self.volume)
        self.surface.ComputeNormalsOn()
        self.surface.GenerateValues(1, (10, 20))
        self.ren = vtk.vtkRenderer()
        self.ren.SetBackground(colors.GetColor3d("Orange"))
        self.vtkWidget.GetRenderWindow().AddRenderer(self.ren)
        self.iren = self.vtkWidget.GetRenderWindow().GetInteractor()
        # Create a mapper
        self.mapper = vtk.vtkPolyDataMapper()
        self.mapper.SetInputConnection(self.surface.GetOutputPort())
        # Create an actor
        self.actor = vtk.vtkActor()
        self.actor.SetMapper(self.mapper)
        self.ren.AddActor(self.actor)

        self.ren.ResetCamera()

        self.frame.setLayout(self.vl)
        self.setCentralWidget(self.frame)

        self.show()
        self.iren.Initialize()
        input("press enter to start")
        threading.Thread(target=lambda: self.run()).start()

    def run(self):
        while not self.window_closed.is_set():
            # data = 100*np.random.rand(50, 50, 200).astype(np.float32)
            # data[:, 10:40, 150:170] = 10.0
            self.sess.run()
            data = self.buffer.get()[0]
            volume_data = vtk.util.numpy_support.numpy_to_vtk(data.ravel(), deep=True)
            print("GOT new data!")
            self.volume.GetPointData().SetScalars(volume_data)
            self.volume.GetPointData().Update()
            self.vtkWidget.Render()
        print("Main thread exited")
        self.sess.close()

    def closeEvent(self, QCloseEvent):
        self.window_closed.set()
        super().closeEvent(QCloseEvent)

    def initialize(self):
        self.sess = arrus.Session("/home/pjarosik/src/arrus-toolkit/examples/rca/us4r.prototxt")
        us4r = self.sess.get_device("/Us4R:0")
        us4r.set_hv_voltage(tx_voltage)
        seq = get_sequence(angles)
        # Declare the complete scheme to execute on the devices.
        speed_of_sound = 1450
        downsampling_factor = 1
        fs = 65e6
        sampling_time = np.arange(start=round(400 / downsampling_factor),
                                  stop=n_samples,
                                  step=round(150 / downsampling_factor)) / fs
        tgc_start = 14
        tgc_slope = 4e2
        distance = sampling_time * speed_of_sound
        tgc_values = tgc_start + distance * tgc_slope
        us4r.set_tgc(tgc_values)

        taps = scipy.signal.firwin(
            numtaps=31,
            cutoff=[0.5*center_frequency, 1.5*center_frequency],
            pass_zero=False,
            fs=65e6/downsampling_factor
        )

        x_grid = np.arange(-8, 8, 0.2)*1e-3  # [m]
        y_grid = np.arange(-8, 8, 0.2)*1e-3  # [m]
        z_grid = np.arange(5, 55, 0.1)*1e-3  # [m]
        rca_rec = ReconstructRCA(output_grid=(x_grid, y_grid, z_grid),
                             angles=angles,
                             speed_of_sound=c)

        scheme = Scheme(
            # Run the provided sequence.
            tx_rx_sequence=seq,
            # Processing pipeline to perform on the GPU device.
            processing=Pipeline(
                steps=(
                    RemapToLogicalOrder(),
                    Transpose(axes=(0, 1, 3, 2)),
                    FirFilter(taps),
                    rca_rec,
                    Mean(axis=0),
                    Hilbert(),
                    EnvelopeDetection(),
                    Lambda(lambda data: data/cp.nanmax(data)),
                    LogCompression(),
                    Lambda(lambda data: data+30.0),
                    # # Envelope compounding
                    Transpose((2, 0, 1)),  # (y, x, z) -> (z, y, x)
                    Lambda(lambda data: cupyx.scipy.ndimage.median_filter(data, size=5)),
                    DynamicRangeAdjustment(0, 30),
                    Lambda(lambda data: (print(f"{np.min(data)} {np.max(data)}"), data)[1]),
                ),
                placement="/GPU:0"
            ),
            work_mode="MANUAL")
        # Upload the scheme on the us4r-lite device.
        self.buffer, self.metadata = self.sess.upload(scheme)


# When we exit the above scope, the session and scheme is properly closed.
print("Stopping the example.")

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    sys.exit(app.exec_())
    print("Exiting")
