# Kinect v2 sensor

VisionRig treats Microsoft Kinect v2 (Xbox One / Kinect for Windows v2) as a
first-class camera sensor, not as a special perception pipeline.

## What enters VisionRig

- RGB: primary image payload used by the existing detector/OCR/landmark/embedding stages.
- Hardware depth: metric depth from Kinect's depth sensor, coordinate-mapped into color space and normalized into VisionRig's existing relative-depth v2 contract.
- Infrared: frame-local auxiliary sensor channel for future IR-aware stages.

This keeps model stages camera-agnostic while allowing hardware depth to replace
monocular depth guesses when a Kinect is present.

## Driver

The adapter uses optional kinect-next 2.x. It supports current Python versions
and exposes synchronized color/depth/IR frames plus the Kinect v2 coordinate
mapper. VisionRig imports it lazily, so normal deployments do not acquire a
Kinect dependency.

Windows requirements:

1. Kinect v2 / Xbox One Kinect sensor.
2. Kinect power + USB adapter.
3. A USB 3.0 port/controller.
4. Kinect for Windows SDK 2.0 / driver.
5. Python 3.11+ as required by VisionRig.

Install:

~~~powershell
pip install -e ".[kinect-v2]"
~~~

Run:

~~~powershell
visionrig-capture --kinect-v2 --model-manifest .\models\yolo.json --landmarks --verbose
~~~

The Kinect hardware-depth stage is enabled automatically by --kinect-v2.

## Contract boundary

PerceptionEvent/v2 currently exposes relative depth rather than meters.
VisionRig therefore normalizes Kinect's measured distance across a configurable
0.5-4.5 m working range. The raw metric depth remains frame-local and does not
cross the ModelRig / Consciousness Core authority boundary.

A future public-contract revision can expose metric distance explicitly without
forcing the RGB pipeline to depend on Kinect-specific types.

## Failure behavior

If the optional driver is absent, the Kinect adapter fails with a targeted
installation message. If the sensor cannot open, VisionRig reports the SDK /
power / USB 3.0 prerequisites instead of silently falling back to webcam input.
