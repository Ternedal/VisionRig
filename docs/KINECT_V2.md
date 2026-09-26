# Kinect v2 sensor

VisionRig treats Microsoft Kinect v2 (Xbox One / Kinect for Windows v2) as a
first-class camera sensor, not as a special perception pipeline.

## What enters VisionRig

- RGB: primary image payload used by the existing detector/OCR/landmark/embedding stages.
- Hardware depth: metric depth from Kinect's depth sensor, coordinate-mapped into color space and emitted as both `distance_m` and normalized `relative_depth`.
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

Run unmanaged:

~~~powershell
visionrig-capture --kinect-v2 --model-manifest .\models\yolo.json --landmarks --verbose
~~~

Run under the shared sensor control plane:

~~~powershell
$env:VISIONRIG_GATEWAY_URL="http://127.0.0.1:8111"
$env:VISIONRIG_PRODUCER_TOKEN="<gateway token>"
visionrig-capture --kinect-v2 --source-id kinect-living-room --control-gateway-url $env:VISIONRIG_GATEWAY_URL --model-manifest .\models\yolo.json --landmarks --verbose
~~~

In managed mode VisionRig checks desired state before the Kinect is opened. A
disabled sensor stays physically closed while heartbeat reports
`capture_active=false`. Re-enabling reopens the Kinect and local event
`frame_sequence` remains monotonic across reopen cycles.

RGB, hardware depth and IR remain local to the capture/perception process; they
are not flattened into the remote JPEG producer protocol. Only heartbeat,
capabilities and effective capture state cross the sensor gateway.

The Kinect hardware-depth stage is enabled automatically by `--kinect-v2`.

## Contract boundary

PerceptionEvent/v3 exposes optional metric distance without forcing generic
camera/model stages to become Kinect-specific. Kinect therefore emits
`distance_m` from measured hardware depth and also keeps `relative_depth`
normalized across a configurable 0.5-4.5 m working range.

Raw depth and IR arrays remain frame-local. Only typed per-entity observations
cross the VisionRig boundary. Spatial fusion may use metric distances to emit
`in_front_of` / `behind` when the measured gap is large enough.

## Failure behavior

If the optional driver is absent, the Kinect adapter fails with a targeted
installation message. If the sensor cannot open, VisionRig reports the SDK /
power / USB 3.0 prerequisites instead of silently falling back to webcam input.
