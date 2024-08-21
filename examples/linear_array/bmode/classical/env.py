import arrus.medium
import arrus.utils.imaging
from arrus.utils.imaging import *
from arrus.ops.imaging import *
from arrus.ops.us4r import *
from gui4us.model.envs.arrus import (
    UltrasoundEnv,
    ArrusEnvConfiguration,
    Curve,
    get_depth_range
)


class ToGrayscaleImg2(arrus.utils.imaging.Operation):
    """
    Converts data to grayscale image (uint8).

    The dynamic range set in the configuration file is assumed to be
    a given contrast value.
    """

    def __init__(self, dr_min, dr_max, contrast=50.0):
        self.xp = None
        self.dr_min = dr_min
        self.dr_max = dr_max
        self._contrast = contrast
        # Translate dr min/max to contrast setting
        contrast_range = self.dr_max-self.dr_min
        contrast_center = self.dr_min + contrast_range/2
        d_contrast = 1  # resolution of 1 dB
        # DR min range: [minimum, contrast_center-contrast_resolution]
        dr_min_max = contrast_center-d_contrast  # contrast 1.0
        self.dr_min_range = (dr_min_max-self.dr_min)/(self._contrast/100)
        self.dr_min_min = dr_min_max-self.dr_min_range  # contrast 0.0
        # DR max rage: [contrast_center, maximum)
        dr_max_min = contrast_center  # contrast 1.0
        self.dr_max_range = (self.dr_max-dr_max_min)/(self._contrast/100)
        self.dr_max_max = dr_max_min + self.dr_max_range  # contrast 0.0

    def set_pkgs(self, num_pkg, **kwargs):
        import cupy as cp
        self.xp = cp

    def prepare(self, const_metadata: arrus.metadata.ConstMetadata):
        return const_metadata.copy(dtype=np.uint8)

    def process(self, data):
        data = self.xp.clip(data, self.dr_min, self.dr_max)
        data = data-self.dr_min
        data = data/(self.dr_max-self.dr_min)*255
        return data.astype(self.xp.uint8)

    @property
    def contrast(self):
        return self._contrast

    @contrast.setter
    def contrast(self, value):
        self._contrast = value
        value = value / 100
        self.dr_min = self.dr_min_min + value*self.dr_min_range
        self.dr_max = self.dr_max_max - value*self.dr_max_range

    def set_parameter(self, key: str, value: Sequence[Number]):
        if not hasattr(self, key):
            raise ValueError(f"{type(self).__name__} has no {key} parameter.")
        setattr(self, key, value)

    def get_parameter(self, key: str) -> Sequence[Number]:
        if not hasattr(self, key):
            raise ValueError(f"{type(self).__name__} has no {key} parameter.")
        return getattr(self, key)

    def get_parameters(self) -> Dict[str, ParameterDef]:
        return {
            "dr_min": ParameterDef(
                name="dr_min",
                space=Box(
                    shape=(1,),
                    dtype=np.float32,
                    unit=Unit.dB,
                    low=-np.inf,
                    high=np.inf
                ),
            ),
            "dr_max": ParameterDef(
                name="dr_max",
                space=Box(
                    shape=(1,),
                    dtype=np.float32,
                    unit=Unit.dB,
                    low=-np.inf,
                    high=np.inf
                ),
            ),
            "contrast": ParameterDef(
                name="contrast",
                space=Box(
                    shape=(1,),
                    dtype=np.float32,
                    unit=Unit.dB,
                    low=0.0,
                    high=100.0
                ),
            )
        }


tx_focuses = [
    arrus.Constant(value=i * 1e-3, placement="/Us4R:0", name=f"sequence:0/txFocus:{i}")
    for i in range(10, 20)
]


def configure(session: arrus.Session):
    medium = arrus.medium.Medium(name="ats549", speed_of_sound=1550)
    probe_model = session.get_device("/Us4R:0").get_probe_model()
    # Imaging grid.
    x_grid = np.arange(probe_model.x_min, probe_model.x_max, 0.05e-3)
    z_grid = np.arange(0e-3, 20e-3, 0.05e-3)

    # Initial TGC curve.
    tgc_sampling_points = np.linspace(np.min(z_grid), np.max(z_grid), 10)
    tgc_values = np.linspace(14, 54, 10)



    sequence = LinSequence(
        tx_aperture_center_element=np.arange(0, probe_model.n_elements),
        tx_aperture_size=64,
        tx_focus=10e-3,
        pulse=Pulse(center_frequency=18e6, n_periods=2, inverse=False),
        rx_aperture_center_element=np.arange(0, probe_model.n_elements),
        rx_aperture_size=64,
        rx_depth_range=get_depth_range(z_grid),
        pri=200e-6,
        speed_of_sound=medium.speed_of_sound)

    pipeline = Pipeline(
            steps=(
                Output(),
                # Channel data pre-processing.
                RemapToLogicalOrder(),
                Transpose(axes=(0, 1, 3, 2)),
                BandpassFilter(),
                QuadratureDemodulation(),
                Decimation(decimation_factor=1),
                # # Data beamforming.
                RxBeamforming(),
                # # Post-processing to B-mode image.
                EnvelopeDetection(),
                Transpose(axes=(0, 2, 1)),
                ScanConversion(x_grid, z_grid),
                Mean(axis=0),
                LogCompression(),
                ToGrayscaleImg2(20, 80),
            ),
            placement="/GPU:0")

    return ArrusEnvConfiguration(
        medium=medium,
        scheme=arrus.ops.us4r.Scheme(
            tx_rx_sequence=sequence,
            processing=pipeline,
            constants=tx_focuses
        ),
        tgc=Curve(points=tgc_sampling_points, values=tgc_values),
        voltage=5,
    )


ENV = UltrasoundEnv(
    session_cfg="C:/Users/user/Documents/Github/arrus-toolkit/us4r.prototxt",
    configure=configure,
    tx_focuses=tx_focuses
)
