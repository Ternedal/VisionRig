# Kinect v2 sensor

VisionRig treats Microsoft Kinect v2 (Xbox One / Kinect for Windows v2) as a
first-class camera sensor, not as a special perception pipeline.

## What enters VisionRig

- RGB: primary image payload used by the existing detector/OCR/landmark/embedding stages.
- Hardware depth: metric depth from Kinect's depth sensor, coordinate-mapped into color space and emitted as both `distance_m` and normalized `relative_depth`.
- Infrared: frame-local auxiliary sensor channel summarized by the bounded
  `infrared_summary` perception stage; raw IR pixels never leave VisionRig.

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

Local Kinect capture still passes RGB, hardware depth and IR directly as
frame-local sensor data. For remote transport, VisionRig now supports
SensorPacket v1/v2 through the authenticated gateway: encoded RGB plus optional
color-aligned uint16 metric depth and uint16 IR. Raw planes remain frame-local
after decode and are never exposed through PerceptionEvent.

VisionRig 0.39.0 adds the dedicated remote producer path. The Kinect adapter
uses the SDK mapper on the producer machine to build a dense color-aligned
uint16 millimeter plane. `visionrig-producer --kinect-v2` JPEG-encodes RGB,
packages aligned depth plus IR as SensorPacket/v2 with adaptive per-plane
compression, and sends it through the authenticated gateway using the same durable
sequence/drop state as other producers. The server deliberately remains independent of Kinect SDK calibration
objects.

Run remote producer mode:

~~~powershell
$env:VISIONRIG_GATEWAY_URL="http://<VisionRig-Tailscale-IP>:8111"
$env:VISIONRIG_PRODUCER_TOKEN="<gateway token>"
pip install -e ".[producer,kinect-v2]"
visionrig-producer --kinect-v2 --source-id kinect-living-room --fps 5 --verbose
~~~

The Kinect hardware-depth stage is enabled automatically by `--kinect-v2`.

## Contract boundary

PerceptionEvent/v4 exposes optional metric distance without forcing generic
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


## Remote packet budget

VisionRig 0.44.0 adds producer-side packet budgeting to remote Kinect mode.
`--max-packet-bytes` defaults to 8 MiB and may also be supplied through
`VISIONRIG_PRODUCER_MAX_PACKET_BYTES`. The producer attempts to keep packets
below the 80% warning threshold by lowering RGB JPEG quality from the configured
`--jpeg-quality` toward `--min-jpeg-quality` in 5-point steps. Metric depth
and IR are left byte-for-byte semantically intact apart from the existing
lossless per-plane compression.

Budget adaptation happens before the durable producer sequence reservation.
An irreducibly oversized packet therefore stops visibly instead of consuming a
sequence that was never sent.


## Physical release acceptance

VisionRig 0.81.0 adds a fail-closed physical acceptance command for the
`visionrig_physical_perception` system release gate. It is deliberately not a
CI hardware simulation.

Run it from an **exact clean checkout** whose SHA is the release candidate:

~~~powershell
$Sha = (git rev-parse HEAD).Trim()
visionrig-kinect-acceptance `
  --expected-sha $Sha `
  --model-manifest .\models\yolo.json `
  --modelrig-worker-url http://127.0.0.1:8099 `
  --source-id kinect-living-room `
  --frames 5
~~~

A PASS requires, in one bounded run:

- real Kinect v2 RGB on every accepted frame;
- raw hardware depth on every accepted frame;
- color-aligned depth matching RGB dimensions on every accepted frame;
- infrared signal on every accepted frame;
- strictly contiguous Kinect frame sequence;
- at least one meaningful semantic VisionRig observation;
- at least one exact-bound ModelRig VisionRig-admission receipt; and
- at least one ModelRig receipt with `world_changed=true`.

The receipt defaults to
`validation/visionrig-kinect-physical-acceptance.json` and contains only
revision, counts, source metadata and semantic/WorldEvidence references. Raw RGB,
depth and infrared bytes are never persisted by this acceptance command.

The command fails instead of writing a PASS receipt when hardware modalities are
missing, perception is empty, ModelRig is unavailable/rejects the event, the
WorldState does not change, the checkout is dirty, or HEAD differs from
`--expected-sha`.

This receipt is evidence for the cross-repository release gate only. It does not
grant identity, durable-memory, execution, scheduling or production authority.


## Infrared perception summary

VisionRig 0.87.0 activates Kinect infrared as a bounded perception signal.
`InfraredSummaryStage` consumes the frame-local uint16 infrared plane and emits
one `InfraredObservation` with normalized mean intensity, contrast, hotspot
fraction and sample count.

The stage does not retain or serialize the raw plane. Small frame-to-frame IR
jitter is bucketed by the ModelRig semantic change gate so normal sensor noise
does not create cognition traffic.


## IR-qualified release evidence

VisionRig 0.88.0 advances the physical Kinect acceptance receipt to `visionrig/kinect-physical-acceptance/v3`. A PASS now requires every accepted physical frame to produce at least one bounded `InfraredObservation` in `visionrig/perception-event/v4`, in addition to the existing raw IR signal check. The receipt records `infrared_semantic_frames`, `infrared_observations`, and `perception_schema`. Non-IR semantic perception is still required independently, so a valid IR summary by itself cannot satisfy the semantic release criterion.


## Fresh ModelRig admission in physical Kinect acceptance

VisionRig 0.93.0 requires the dedicated Kinect physical acceptance run to receive a fresh ModelRig admission. A `replayed` bridge result or a receipt with `replayed=true` fails the run immediately, even when the event binding and WorldState fields are otherwise valid. This keeps dedicated Kinect release evidence aligned with the generic physical qualification gate.


## Cognition-qualified physical acceptance

VisionRig 0.98.0 advances the Kinect physical receipt to `visionrig/kinect-physical-acceptance/v3`. Every exact-bound fresh ModelRig receipt used by the Kinect gate must now expose a canonical `world-evidence-event:<64hex>` evidence ref, prove `cognition_event_queued=true`, and expose a canonical `cevt-<32hex>` cognition event id. The receipt records cognition-queued receipt count and the bounded cognition event ids alongside existing WorldState evidence.
