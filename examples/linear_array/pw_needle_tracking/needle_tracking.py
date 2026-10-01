"""Plane-wave needle tracking: needle line detection (GPU, cupy) and the TX angle selection.

The angles convention (image coordinates: OX -- along the probe, OZ -- depth, pointing down):

- needle line angle ``alpha`` [deg]: the angle between the needle and the OX axis, positive when the needle goes
  deeper with increasing x (i.e. descends to the right in the image),
- TX angle ``theta`` [deg]: the plane wave steering angle as in ARRUS (``Tx.angle``, PwiSequence.angles), i.e. the
  wave propagates in the direction (sin(theta), cos(theta)) in the (x, z) plane.

The needle is a specular reflector: the echo goes back to the probe when the plane wave propagates perpendicularly
to the needle, i.e. when (sin(theta), cos(theta)) is parallel to the needle normal (-sin(alpha), cos(alpha)),
which gives ``theta = -alpha`` (see ``tx_angle_for_needle``).
"""
import collections
import dataclasses
import math
import time
from typing import Optional, Sequence

import numpy as np

import arrus.kernels.simple_tx_rx_sequence
import arrus.kernels.tx_rx_sequence
import arrus.metadata
from arrus.ops.imaging import PwiSequence
from arrus.ops.us4r import TxRxSequence
from arrus.utils.imaging import Operation, ReconstructLri, get_unique_probe_model


def tx_angle_for_needle(needle_angle_deg: float) -> float:
    """Returns the TX angle [deg] of the plane wave perpendicular to the needle with the given angle [deg]."""
    return -needle_angle_deg


@dataclasses.dataclass(frozen=True)
class DetectorParams:
    """
    Needle line detection parameters.

    :param smoothing_sigma: gaussian smoothing of the B-mode image [pixels] (speckle suppression)
    :param z_min: the needle is not searched above this depth [m] (removes the ring-down/near-field region)
    :param intensity_drop: candidate needle pixels: at most this many dB below the image maximum [dB]
    :param max_points: at most this number of the brightest candidate pixels are used in the Hough transform
    :param line_angles: needle line angles considered in the Hough transform [deg]
    :param min_abs_line_angle: lines with a smaller absolute angle are ignored [deg] (e.g. to ignore horizontal
      specular layers, like the phantom bottom); 0 means that all angles are accepted
    :param rho_resolution: Hough transform distance resolution [m]
    :param inlier_distance: max. distance of a pixel from the detected line to be counted as the line pixel [m]
    :param min_inliers: min. number of line pixels
    :param min_length: min. length of the detected line (the extent of the line pixels) [m]
    :param min_contrast: min. ratio of the Hough peak to the median of the per-angle peaks (how much the detected
      line stands out from the speckle, for which all the angles give similar peaks)
    """
    smoothing_sigma: float = 1.5
    z_min: float = 3e-3
    intensity_drop: float = 12.0
    max_points: int = 4000
    line_angles: Sequence[float] = tuple(np.arange(-60, 60.25, 0.5))
    min_abs_line_angle: float = 0.0
    rho_resolution: float = 0.2e-3
    inlier_distance: float = 0.4e-3
    min_inliers: int = 40
    min_length: float = 5e-3
    min_contrast: float = 3.0


@dataclasses.dataclass(frozen=True)
class LineDetection:
    """The detected needle line: angle [deg], a point on the line (x, z) [m], length [m], contrast, #inliers."""
    angle: float
    x: float
    z: float
    length: float
    contrast: float
    n_inliers: int


class NeedleLineDetector:
    """
    Detects the needle (the brightest, dominant straight line) in the B-mode image, with the Hough transform.

    Steps (all on the GPU): gaussian smoothing -> candidate pixels (the brightest pixels, below the near-field
    region) -> Hough transform with intensity-weighted votes -> the peak, its contrast against the speckle ->
    the line pixels (inliers) -> least-squares (PCA) refinement of the line angle -> acceptance criteria
    (#inliers, line length, contrast, angle range).
    """

    def __init__(self, x_grid, z_grid, params: DetectorParams = DetectorParams(), xp=None):
        if xp is None:
            import cupy as xp
        self.xp = xp
        self.params = params
        if xp.__name__ == "cupy":
            import cupyx.scipy.ndimage as ndimage
        else:
            import scipy.ndimage as ndimage
        self.ndimage = ndimage
        self.x_grid = np.asarray(x_grid, dtype=np.float32)
        self.z_grid = np.asarray(z_grid, dtype=np.float32)
        nz, nx = len(self.z_grid), len(self.x_grid)
        self.shape = (nz, nx)
        z, x = np.meshgrid(self.z_grid, self.x_grid, indexing="ij")  # (nz, nx)
        self._x = xp.asarray(x.ravel())
        self._z = xp.asarray(z.ravel())
        self._near_field = xp.asarray(z < params.z_min)
        # Hough: line angle alpha, line normal (-sin(alpha), cos(alpha)), rho = -x*sin(alpha) + z*cos(alpha).
        angles = np.asarray(params.line_angles, dtype=np.float64)
        if params.min_abs_line_angle > 0:
            angles = angles[np.abs(angles) >= params.min_abs_line_angle]
        self.line_angles = angles
        alpha = np.deg2rad(angles)
        self._nx = xp.asarray(-np.sin(alpha), dtype=xp.float32)[:, None]
        self._nz = xp.asarray(np.cos(alpha), dtype=xp.float32)[:, None]
        rho_max = float(np.hypot(np.max(np.abs(self.x_grid)), np.max(np.abs(self.z_grid))))
        self._rho_min = -rho_max
        self._n_rho = int(math.ceil(2*rho_max/params.rho_resolution)) + 1
        self._angle_ids = xp.arange(len(angles), dtype=xp.int64)[:, None]

    def detect(self, bmode) -> Optional[LineDetection]:
        """
        :param bmode: B-mode image (nz, nx) [dB], on the GPU (cupy array)
        :return: the detected line, or None
        """
        xp, p = self.xp, self.params
        img = xp.asarray(bmode, dtype=xp.float32)
        if img.shape != self.shape:
            raise ValueError(f"Expected image of shape {self.shape}, got {img.shape}")
        img = xp.nan_to_num(img, nan=-300.0, neginf=-300.0, posinf=-300.0)
        if p.smoothing_sigma > 0:
            img = self.ndimage.gaussian_filter(img, sigma=p.smoothing_sigma)
        img = xp.where(self._near_field, -xp.inf, img).ravel()
        vmax = img.max()
        threshold = vmax - p.intensity_drop
        idx = xp.nonzero(img >= threshold)[0]
        if int(idx.size) < p.min_inliers:
            return None
        values = img[idx]
        if int(idx.size) > p.max_points:
            top = xp.argpartition(values, -p.max_points)[-p.max_points:]
            idx, values = idx[top], values[top]
        weights = (values - threshold)/p.intensity_drop + 0.1
        x, z = self._x[idx], self._z[idx]
        # Hough transform: (n angles, n points)
        rho = self._nx*x[None, :] + self._nz*z[None, :]
        rho_ids = xp.rint((rho - self._rho_min)/p.rho_resolution).astype(xp.int64)
        flat_ids = (self._angle_ids*self._n_rho + rho_ids).ravel()
        n_angles = len(self.line_angles)
        acc = xp.bincount(flat_ids, weights=xp.broadcast_to(weights[None, :], rho.shape).ravel(),
                          minlength=n_angles*self._n_rho).reshape(n_angles, self._n_rho)
        per_angle_max = acc.max(axis=1)
        peak_id = int(acc.argmax())
        angle_id, rho_id = divmod(peak_id, self._n_rho)
        peak = acc[angle_id, rho_id]
        contrast = float(peak/xp.maximum(xp.median(per_angle_max), 1e-9))
        # Line pixels.
        rho_peak = self._rho_min + rho_id*p.rho_resolution
        inliers = xp.abs(rho[angle_id] - rho_peak) <= p.inlier_distance
        n_inliers = int(inliers.sum())
        if n_inliers < p.min_inliers or contrast < p.min_contrast:
            return None
        xi, zi, wi = x[inliers], z[inliers], weights[inliers]
        # Refinement: the principal direction of the (weighted) line pixels.
        w_sum = wi.sum()
        xm, zm = (wi*xi).sum()/w_sum, (wi*zi).sum()/w_sum
        dx, dz = xi - xm, zi - zm
        cxx, czz, cxz = (wi*dx*dx).sum()/w_sum, (wi*dz*dz).sum()/w_sum, (wi*dx*dz).sum()/w_sum
        cxx, czz, cxz, xm, zm = (float(v) for v in (cxx, czz, cxz, xm, zm))
        alpha = 0.5*math.atan2(2*cxz, cxx - czz)  # [-pi/2, pi/2]
        direction = (math.cos(alpha), math.sin(alpha))
        t = dx*direction[0] + dz*direction[1]
        length = float(t.max() - t.min())
        alpha_deg = math.degrees(alpha)
        if length < p.min_length:
            return None
        if abs(alpha_deg) < p.min_abs_line_angle or abs(alpha_deg) > np.max(np.abs(self.line_angles)):
            return None
        return LineDetection(angle=alpha_deg, x=xm, z=zm, length=length, contrast=contrast, n_inliers=n_inliers)


class TxAngleSelector:
    """
    Selects the TX angle to apply on the hardware, based on the buffer of the recently detected angles.

    After each frame: the TX angle perpendicular to the detected needle (or None, when no needle was detected) is
    put into the buffer, then the next TX angle is determined:

    - the buffer is empty or all None: exploration -- the next angle from the sweep min -> max (with wrapping);
      each time the exploration (re)starts, the sweep starts from the min angle,
    - otherwise: the most recent non-None angle in the buffer (snapped to the available TX angles).

    :param tx_angles: available TX angles [deg] (e.g. uploaded TX delay profiles), increasing
    :param buffer_size: the number of the most recent frames kept in the buffer
    """

    def __init__(self, tx_angles, buffer_size: int = 5):
        self.tx_angles = np.asarray(tx_angles, dtype=np.float64)
        self.buffer = collections.deque(maxlen=buffer_size)
        self._explore_id = None  # None: not exploring

    @property
    def is_exploring(self):
        return self._explore_id is not None

    def snap(self, angle: float) -> float:
        """The available TX angle closest to the given one (angles outside the range are clipped)."""
        return float(self.tx_angles[self.snap_id(angle)])

    def snap_id(self, angle: float) -> int:
        return int(np.argmin(np.abs(self.tx_angles - angle)))

    def update(self, detected_tx_angle: Optional[float]) -> float:
        """
        :param detected_tx_angle: the TX angle perpendicular to the needle detected in the last frame [deg],
          or None when no needle was detected
        :return: the next TX angle to apply [deg]
        """
        self.buffer.append(detected_tx_angle)
        return self.next_angle()

    def next_angle(self) -> float:
        detected = [a for a in self.buffer if a is not None]
        if not detected:
            # Exploration: min -> max, wrapping.
            self._explore_id = 0 if self._explore_id is None else (self._explore_id + 1) % len(self.tx_angles)
            return float(self.tx_angles[self._explore_id])
        self._explore_id = None
        return self.snap(detected[-1])


class SteeredPwReconstructLri(ReconstructLri):
    """
    ReconstructLri for a PWI sequence (PwiSequence or a TxRxSequence with TX angles), whose TX angles can be changed
    at runtime with the TX delay profiles (``Constant("/{sequence}/txAngle:{i}")``).

    Only the transmit angle and the TX center delay depend on the profile; both are precomputed for each profile in
    ``prepare``, so selecting a profile is just a matter of replacing a reference (``select_profile``).

    :param tx_angles: profile number -> TX angle(s) [rad]: a scalar or one value for each TX/RX of the sequence
    """

    def __init__(self, x_grid, z_grid, tx_angles, rx_tang_limits=None):
        super().__init__(x_grid=x_grid, z_grid=z_grid, rx_tang_limits=rx_tang_limits)
        self.tx_angles = list(tx_angles)
        self._profiles = None
        self._current = None

    def prepare(self, const_metadata):
        output_metadata = super().prepare(const_metadata)
        seq = const_metadata.context.sequence
        probe_model = get_unique_probe_model(const_metadata)
        fs = const_metadata.data_description.sampling_frequency

        if isinstance(seq, PwiSequence):
            default_angles = seq.angles

            def get_center_delay(angles):
                return arrus.kernels.simple_tx_rx_sequence.get_center_delay(
                    sequence=dataclasses.replace(seq, angles=angles), c=seq.speed_of_sound,
                    probe_model=probe_model, fs=fs)
        elif isinstance(seq, TxRxSequence):
            # Only the RX-active TX/RXs (the same as in ReconstructLri).
            ops = [op for op in seq.ops if op.rx.aperture.size is None or op.rx.aperture.size > 0]
            seq = dataclasses.replace(seq, ops=ops)
            default_angles = [op.tx.angle for op in ops]

            def get_center_delay(angles):
                new_ops = [dataclasses.replace(op, tx=dataclasses.replace(op.tx, angle=float(a)))
                           for op, a in zip(ops, self._broadcast(angles))]
                return arrus.kernels.tx_rx_sequence.get_center_delay(
                    sequence=dataclasses.replace(seq, ops=new_ops), probe_tx=probe_model, probe_rx=probe_model)
        else:
            raise ValueError(f"Unsupported sequence: {type(seq)}")

        # The parameters set by ReconstructLri.prepare are for the sequence (default) angles.
        default_angles = self._broadcast(default_angles)
        default_center_delay = get_center_delay(default_angles)
        tx_aperture_center_angles = self.num_pkg.asnumpy(self.tx_ang_zx) - default_angles
        self._profiles = []
        for angles in self.tx_angles:
            angles = self._broadcast(angles)
            tx_ang_zx = self.num_pkg.asarray(tx_aperture_center_angles + angles, dtype=self.num_pkg.float32)
            initial_delay = self.num_pkg.float32(
                self.initial_delay - default_center_delay + get_center_delay(angles))
            self._profiles.append((tx_ang_zx, initial_delay))
        # The sequence (default) delays are applied until a profile is selected.
        self._current = (self.tx_ang_zx, self.initial_delay)
        return output_metadata

    def select_profile(self, profile: int):
        # A single reference assignment: process() never sees an angle mismatched with the delay.
        self._current = self._profiles[profile]

    def process(self, data):
        self.tx_ang_zx, self.initial_delay = self._current
        return super().process(data)

    def _broadcast(self, angles):
        angles = np.atleast_1d(np.asarray(angles, dtype=np.float64)).flatten()
        if len(angles) == 1:
            angles = np.repeat(angles, self.n_tx)
        if len(angles) != self.n_tx:
            raise ValueError(f"Expected a scalar or {self.n_tx} TX angles, got: {len(angles)}")
        return angles


class NeedleTxAngleSelection(Operation):
    """
    Pipeline step: detects the needle in the B-mode image (the input, (nz, nx) [dB]), updates the buffer of the
    detected angles and determines the TX angle for the next acquisition (``next_tx_angle``).

    The output of this step is the history of the TX angles: an array (2, history size) [deg]: the TX angle applied
    on the hardware (row 0) and the TX angle perpendicular to the detected needle (row 1, NaN: no detection),
    the most recent frame is the last one.

    Before each acquisition, the acquisition loop should set the TX angle actually applied (``acquisition_tx_angle``)
    and after the frame was processed read ``next_tx_angle``.
    """

    def __init__(self, x_grid, z_grid, tx_angles, detector_params: DetectorParams = DetectorParams(),
                 buffer_size: int = 5, history_size: int = 200, name=None):
        super().__init__(name=name)
        self.x_grid, self.z_grid = x_grid, z_grid
        self.detector_params = detector_params
        self.selector = TxAngleSelector(tx_angles=tx_angles, buffer_size=buffer_size)
        self.history_size = history_size
        # Nothing detected yet: start with the exploration.
        self.next_tx_angle = self.selector.next_angle()
        self.acquisition_tx_angle = self.next_tx_angle
        self.last_detection: Optional[LineDetection] = None
        self.last_processing_time = 0.0  # [s]
        self._history = np.full((2, history_size), np.nan, dtype=np.float32)
        self.detector = None
        self.xp = None

    def set_pkgs(self, num_pkg, **kwargs):
        self.xp = num_pkg

    def prepare(self, const_metadata):
        if const_metadata.input_shape != (len(self.z_grid), len(self.x_grid)):
            raise ValueError(f"Expected B-mode image (nz, nx) = {(len(self.z_grid), len(self.x_grid))}, "
                             f"got: {const_metadata.input_shape}")
        self.detector = NeedleLineDetector(self.x_grid, self.z_grid, self.detector_params, xp=self.xp)
        return const_metadata.copy(
            input_shape=self._history.shape, dtype=np.float32,
            data_desc=dataclasses.replace(const_metadata.data_description, spacing=None))

    def initialize(self, data):
        # Warm up the GPU kernels, without changing the state (the buffer of angles).
        self.detector.detect(data)
        return self.xp.asarray(self._history)

    def process(self, data):
        start = time.perf_counter()
        detection = self.detector.detect(data)
        self.last_detection = detection
        detected_tx_angle = tx_angle_for_needle(detection.angle) if detection is not None else None
        self.next_tx_angle = self.selector.update(detected_tx_angle)
        self._history = np.roll(self._history, -1, axis=1)
        self._history[0, -1] = self.acquisition_tx_angle
        self._history[1, -1] = np.nan if detected_tx_angle is None else detected_tx_angle
        self.last_processing_time = time.perf_counter() - start
        return self.xp.asarray(self._history)
