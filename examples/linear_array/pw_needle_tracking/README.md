# Plane-wave needle tracking

A single steered plane wave per frame; its TX angle follows the needle detected in the B-mode image.

Requires ARRUS with the runtime TX angle selection (`txAngle` TX delay profiles, not in the ARRUS 0.14.2 release);
on 192.168.10.105 the patched build is in `~/src/needle/arrus` (arrus v0.14.x + us4r-api v0.15.x with the
txAngle changes), used without installing it:

```bash
cd arrus-toolkit/examples/linear_array
export PYTHONPATH=~/src/needle/arrus/build/api/python   # the patched ARRUS, for this shell only
gui4us --cfg pw_needle_tracking                   # press Start
python3 -m unittest pw_needle_tracking/test_needle_tracking.py   # offline tests (GPU)
```

Displays: B-mode (top); the selected TX angle per frame and the TX angle perpendicular to the detected needle
(bottom, the most recent frame on the right; a flat line = the angle is held).

## How it works

- `env.py` — `NeedleTrackingEnv` (a `gui4us` `UltrasoundEnv`): a raw `TxRxSequence` with one PW TX/RX (full
  aperture), all TX angles uploaded as TX delay profiles (constants `/needle/txAngle:{i}`), MANUAL work mode.
  Its acquisition thread, for each frame: applies the selected angle
  (`session.set_parameters({"/Us4R:0/needle/txAngle": i})` + the same angle in the beamformer), triggers one
  acquisition, waits for the pipeline. So each frame is beamformed with exactly the angle it was acquired with.
- `needle_tracking.py` — the pipeline steps (cupy):
  - `SteeredPwReconstructLri`: `ReconstructLri` with the TX angle/center delay precomputed per profile,
  - `NeedleTxAngleSelection` (after the B-mode `Output()`): needle detection (`NeedleLineDetector`: gaussian
    smoothing, the brightest pixels below the near field, intensity-weighted Hough transform, peak contrast vs the
    speckle, PCA refinement of the angle, length/#pixels/angle checks), then `TxAngleSelector`: the TX angle
    perpendicular to the needle (`theta = -alpha`) or None goes into the buffer; buffer empty/all None ->
    exploration (min -> max, restarting from min), otherwise the most recent detected angle.
- `needle_config.py` — settings, overridable with environment variables: `ARRUS_SESSION_CFG`
  (default `~/us4r.prototxt`), `ARRUS_VOLTAGE` (5 V), `NEEDLE_TX_ANGLE_MIN/MAX/STEP` (-10, 10, 1 deg),
  `NEEDLE_BUFFER_SIZE` (5), `NEEDLE_MIN_LINE_ANGLE` (3 deg: near-horizontal lines, e.g. layers, are ignored),
  `NEEDLE_SPEED_OF_SOUND` (1450), `NEEDLE_CENTER_FREQUENCY` (6 MHz), `NEEDLE_SHOW_ARRUS_TIMING` (1).

## Terminal output

Each TX angle change prints the Python-side `session.set_parameters` time and (ARRUS DEBUG log) the breakdown:
the us4r-api `SetTxDelays` call for all us4OEMs, trigger stop/start and the settle sleeps; every 5 s a summary
(frames/s, detections, detection time, switch time statistics).

Measured on the Orin + 2 us4OEM+ (10L128, 21 profiles): `set_parameters` ~20.7 ms, of which the us4r-api
`SetTxDelays` is ~0.2-0.3 ms (both OEMs) and ~20.1 ms are the two fixed 10 ms settle sleeps in
`Us4RImpl::setParameters`.
