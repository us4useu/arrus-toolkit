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
from parameters import *


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
        self.sess = arrus.Session("us4r.prototxt")
        us4r = self.sess.get_device("/Us4R:0")
        us4r.set_hv_voltage(tx_voltage)
        probe_model = us4r.get_probe_model()

        tx_focus = [np.inf]
        tx_ang_zx = [0]
        tx_ang_zy = [0]

        self.seq = get_sequence(
            probe_model=probe_model,
            tx_focus=tx_focus, tx_ang_zx=tx_ang_zx, tx_ang_zy=tx_ang_zy)

        self.processing, self.rf_queue = get_imaging(
            sequence=self.seq,
            tx_focus=tx_focus, tx_ang_zx=tx_ang_zx, tx_ang_zy=tx_ang_zy)
        # Declare the complete scheme to execute on the devices.
        self.scheme = Scheme(
            tx_rx_sequence=self.seq,
            processing=self.processing,
            work_mode="MANUAL"
        )
        us4r.set_tgc(tgc_values)
        self.buffer, self.metadata = self.sess.upload(self.scheme)


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    sys.exit(app.exec_())
