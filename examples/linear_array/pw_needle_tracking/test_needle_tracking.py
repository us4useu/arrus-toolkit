"""Offline tests of the needle detection and the TX angle selection (GPU required for the detector tests).

    python3 -m unittest example/pw_needle_tracking/test_needle_tracking.py -v
"""
import math
import os
import sys
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from needle_tracking import DetectorParams, NeedleLineDetector, TxAngleSelector, tx_angle_for_needle  # noqa: E402

X_GRID = np.arange(-32e-3, 32e-3, 0.1e-3)
Z_GRID = np.arange(0, 40e-3, 0.1e-3)


def synthetic_bmode(needle_angle=None, needle_center=(0.0, 20e-3), needle_length=25e-3, needle_db=25.0,
                    horizontal_layer_z=None, layer_db=10.0, seed=0):
    """Speckle (Rayleigh envelope) [dB] + optionally a bright needle segment and a horizontal layer."""
    rng = np.random.default_rng(seed)
    nz, nx = len(Z_GRID), len(X_GRID)
    envelope = np.abs(rng.normal(size=(nz, nx)) + 1j*rng.normal(size=(nz, nx)))
    img = 20*np.log10(envelope + 1e-9) + 40  # ~40 dB speckle
    z, x = np.meshgrid(Z_GRID, X_GRID, indexing="ij")
    if needle_angle is not None:
        a = math.radians(needle_angle)
        xc, zc = needle_center
        # distance from the needle axis and position along it
        d = np.abs(-(x - xc)*math.sin(a) + (z - zc)*math.cos(a))
        t = (x - xc)*math.cos(a) + (z - zc)*math.sin(a)
        needle = (d < 0.25e-3) & (np.abs(t) < needle_length/2)
        img = np.where(needle, img + needle_db, img)
    if horizontal_layer_z is not None:
        layer = np.abs(z - horizontal_layer_z) < 0.2e-3
        img = np.where(layer, img + layer_db, img)
    return img.astype(np.float32)


class NeedleLineDetectorTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import cupy
        cls.cp = cupy
        cls.detector = NeedleLineDetector(X_GRID, Z_GRID, DetectorParams(), xp=cupy)

    def detect(self, img):
        return self.detector.detect(self.cp.asarray(img))

    def test_detects_needle_angle(self):
        for angle in [-40, -25, -10, -3, 5, 15, 30, 45]:
            with self.subTest(angle=angle):
                detection = self.detect(synthetic_bmode(needle_angle=angle, seed=angle + 100))
                self.assertIsNotNone(detection)
                self.assertAlmostEqual(detection.angle, angle, delta=0.5)
                self.assertGreater(detection.length, 20e-3)

    def test_no_needle_in_speckle(self):
        for seed in range(5):
            with self.subTest(seed=seed):
                self.assertIsNone(self.detect(synthetic_bmode(seed=seed)))

    def test_needle_brighter_than_horizontal_layer(self):
        detection = self.detect(synthetic_bmode(needle_angle=20, horizontal_layer_z=30e-3, layer_db=15.0))
        self.assertIsNotNone(detection)
        self.assertAlmostEqual(detection.angle, 20, delta=0.5)

    def test_min_abs_line_angle_ignores_horizontal_layer(self):
        detector = NeedleLineDetector(X_GRID, Z_GRID, DetectorParams(min_abs_line_angle=3.0), xp=self.cp)
        img = synthetic_bmode(horizontal_layer_z=30e-3, layer_db=25.0)
        self.assertIsNotNone(self.detect(img))  # the default detector: the layer is a line
        self.assertIsNone(detector.detect(self.cp.asarray(img)))

    def test_detection_time(self):
        img = self.cp.asarray(synthetic_bmode(needle_angle=20))
        for _ in range(3):
            self.detector.detect(img)
        n = 20
        self.cp.cuda.Device().synchronize()
        start = time.perf_counter()
        for _ in range(n):
            self.detector.detect(img)
        self.cp.cuda.Device().synchronize()
        print(f"\nNeedle detection time ({img.shape} image): {(time.perf_counter() - start)/n*1e3:.2f} ms")


class TxAngleSelectorTest(unittest.TestCase):

    def test_explores_from_min_to_max_when_nothing_detected(self):
        selector = TxAngleSelector(tx_angles=np.arange(-10, 11, 1), buffer_size=3)
        angles = [selector.update(None) for _ in range(23)]
        self.assertEqual(angles, list(range(-10, 11)) + [-10, -9])

    def test_uses_the_most_recent_detected_angle(self):
        selector = TxAngleSelector(tx_angles=np.arange(-10, 11, 1), buffer_size=3)
        selector.update(None)
        self.assertEqual(selector.update(4.2), 4)
        self.assertEqual(selector.update(-3.6), -4)
        # The needle lost for less than the buffer size: keep the last detected angle.
        self.assertEqual(selector.update(None), -4)
        self.assertEqual(selector.update(None), -4)
        # All None: the exploration restarts from the min angle.
        self.assertEqual(selector.update(None), -10)
        self.assertEqual(selector.update(None), -9)

    def test_clips_to_the_available_angles(self):
        selector = TxAngleSelector(tx_angles=np.arange(-10, 11, 1), buffer_size=3)
        self.assertEqual(selector.update(25.0), 10)
        self.assertEqual(selector.update(-25.0), -10)

    def test_tx_angle_perpendicular_to_needle(self):
        # The needle descending to the right (positive angle) requires the wave propagating to the left.
        self.assertEqual(tx_angle_for_needle(20.0), -20.0)
        a = math.radians(20.0)
        theta = math.radians(tx_angle_for_needle(20.0))
        wave = (math.sin(theta), math.cos(theta))
        needle = (math.cos(a), math.sin(a))
        self.assertAlmostEqual(wave[0]*needle[0] + wave[1]*needle[1], 0.0)


if __name__ == "__main__":
    unittest.main()
