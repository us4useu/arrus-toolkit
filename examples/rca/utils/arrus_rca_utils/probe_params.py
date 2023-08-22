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