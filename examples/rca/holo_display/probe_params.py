from dataclasses import dataclass
from arrus.devices.probe import ProbeModel, ProbeModelId


@dataclass(frozen=True)
class ProbeArray:
    """
    RCA probe array description.

    :param n_elements: number of elements in the given array
    :param pitch: the distance between consecutive elements
    :param start: the number of system channel connected to the first element
       of the array
    """
    n_elements: int
    pitch: float
    start: int
    arrangement: str

    def to_arrus_probe(self):
        return ProbeModel(
            model_id=ProbeModelId("us4us", f"RCA_{self.start}"),
            n_elements=self.n_elements,
            pitch=self.pitch, curvature_radius=0
        )


# DEMO parameters: Vermon 128+128 elements
APERTURE_X = ProbeArray(
    n_elements=128,
    pitch=0.2e-3,
    start=0,
    arrangement="ox"
)
APERTURE_Y = ProbeArray(
    n_elements=128,
    pitch=0.2e-3,
    start=128,
    arrangement="oy"
)
