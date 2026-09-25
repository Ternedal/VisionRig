# VisionRig

VisionRig is Kaliv/ModelRig's dedicated visual-perception subsystem.

**Boundary:** VisionRig owns visual input -> structured perception. ModelRig owns
semantic interpretation, durable memory, cognition and Consciousness Core
world-state authority.

## Current state

VisionRig includes:

- typed PerceptionEvent/v3 observations
- local webcam/image/video capture
- Kinect v2 RGB + hardware depth + infrared capture
- bounded camera/screen/VR ingress
- authenticated cross-device sensor gateway
- crash-safe webcam/screen reference producer
- **live sensor/runtime observability for ModelRig/Kaliv UI**
- optional YOLO ONNX detection + short-term tracking
- explicit detector label -> entity-kind mapping
- OCR, pose/hands/face landmarks, relative depth and metric hardware depth
- bounded 2D relations plus sensor-backed front/behind depth ordering
- bounded full-frame/entity embedding sidecar
- encrypted, revisioned .mrvision profiles + image enrollment/revocation CLI
- **service-loaded face/body/object/place recognition hints**
- bounded cursor event journal for ModelRig
- opt-in semantic PerceptionEvent/v3 -> Consciousness Core bridge with change suppression
- verified model manifests with checksum/provenance/license metadata

## Recognition flow

```text
frame
  |
  +--> full-frame embedding --> .mrvision place match --> scene hint
  |
  +--> entities --> crop embeddings --> .mrvision face/body/object match
  |
  v
spatial relations
  |
  v
PerceptionEvent v3
```

Raw pixels and embedding vectors do not enter Consciousness Core. All .mrvision
matches remain non-authoritative.

## Operational status

`GET /api/v1/sensors/status` exposes bounded operational telemetry for remote
camera, screen and VR producers: accepted/rejected totals, active processing,
per-source sequence, accumulated producer drops, device identity and last-seen
UTC. The same snapshot is embedded in `/health`.

See:
- docs/ARCHITECTURE.md
- docs/MODELS.md
- docs/MRVISION.md
- docs/MRVISION_RUNTIME.md
- docs/SPATIAL_SEMANTICS.md
- docs/SENSOR_INGRESS.md
- docs/SENSOR_GATEWAY.md
- docs/PRODUCER.md
- docs/KINECT_V2.md
- docs/PERCEPTION_V3.md
- docs/MODELRIG_BRIDGE.md
- docs/OBSERVABILITY.md
