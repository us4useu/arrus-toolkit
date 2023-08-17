import vtkmodules.vtkInteractionStyle
from vtkmodules.vtkCommonColor import vtkNamedColors
from vtkmodules.vtkCommonDataModel import vtkPiecewiseFunction
from vtkmodules.vtkIOLegacy import vtkStructuredPointsReader
from vtkmodules.vtkRenderingCore import (
    vtkColorTransferFunction,
    vtkRenderWindow,
    vtkRenderWindowInteractor,
    vtkRenderer,
    vtkVolume,
    vtkVolumeProperty
)
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
from vtkmodules.vtkIOImage import vtkPNGWriter
from vtkmodules.vtkRenderingVolume import vtkFixedPointVolumeRayCastMapper
from vtkmodules.vtkCommonDataModel import vtkImageData
import vtk.util.numpy_support
import pickle
import numpy as np
import sys
import matplotlib.pyplot as plt
import time
import scipy.ndimage
import vtk
from vtkmodules.vtkRenderingVolumeOpenGL2 import vtkOpenGLRayCastImageDisplayHelper
from vtk import vtkRenderingLookingGlass


class VTKVisualizer:

    def __init__(self, dimensions, use_lgf=True):
        colors = vtkNamedColors()
        # Create a looking glass render window
        ren1 = vtk.vtkRenderer()
        iren = vtkRenderWindowInteractor()

        if use_lgf:
            self.renWin = vtkRenderingLookingGlass.vtkLookingGlassInterface.CreateLookingGlassRenderWindow()
            if self.renWin.GetDeviceType() == "standard":
                # This looks better on large settings
                self.renWin.SetDeviceType("large")
            num_tiles = np.prod(self.renWin.GetInterface().GetQuiltTiles())
            iren.SetRenderWindow(self.renWin)
            iren.SetDesiredUpdateRate(iren.GetDesiredUpdateRate()*num_tiles)
        else:
            self.renWin = vtkRenderWindow()
            iren.SetRenderWindow(self.renWin)

        self.renWin.AddRenderer(ren1)

        self.data = vtkImageData()
        dimensions = tuple(reversed(dimensions))
        self.data.SetDimensions(*dimensions)
        self.data.SetSpacing([1, 1, 1])
        self.data.SetOrigin([0, 0, 0])

        opacityTransferFunction = vtkPiecewiseFunction()
        opacityTransferFunction.AddPoint(0, 0.0)
        opacityTransferFunction.AddPoint(20, 0.0)
        opacityTransferFunction.AddPoint(120, 1.0)

        # Create transfer mapping scalar value to color.
        colorTransferFunction = vtkColorTransferFunction()
        colorTransferFunction.AddRGBPoint(0.0, 1.0, 1.0, 1.0)

        # The property describes how the data will look.
        volumeProperty = vtkVolumeProperty()
        volumeProperty.SetColor(colorTransferFunction)
        volumeProperty.SetScalarOpacity(opacityTransferFunction)
        volumeProperty.ShadeOn()
        volumeProperty.SetInterpolationTypeToLinear()

        # The mapper / ray cast function know how to render the data.
        volumeMapper = vtk.vtkGPUVolumeRayCastMapper()
        # or vtk.vtkOpenGLGPUVolumeRayCastMapper()
        # or vtkFixedPointVolumeRayCastMapper()
        volumeMapper.SetInputData(self.data)

        # The volume holds the mapper and the property and
        # can be used to position/orient the volume.
        volume = vtkVolume()
        volume.SetMapper(volumeMapper)
        volume.SetProperty(volumeProperty)

        ren1.AddVolume(volume)
        ren1.SetBackground(colors.GetColor3d('Wheat'))
        ren1.GetActiveCamera().Azimuth(-10)
        ren1.GetActiveCamera().Elevation(-10)
        ren1.GetActiveCamera().Roll(-90)
        ren1.ResetCameraClippingRange()
        ren1.ResetCamera()
        iren_style = vtk.vtkInteractorStyleTrackballCamera()
        iren.SetInteractorStyle(iren_style)

    def update(self, volume):
        frames = volume
        gain = np.linspace(1, 3, frames.shape[-1])
        gain = gain.reshape(1, 1, -1)
        # frames = frames*gain
        # frames[frames == -np.inf] = np.max(frames)
        frames = -frames
        frames -= np.min(frames)
        frames = -frames
        frames = frames - np.min(frames)
        frames = frames[:, :, :]
        frames = scipy.ndimage.median_filter(frames, size=5)
        volume_data = vtk.util.numpy_support.numpy_to_vtk(frames.ravel(), deep=True)
        self.data.GetPointData().SetScalars(volume_data)
        self.renWin.Render()

