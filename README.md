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
- **live sensor/runtime observability with online/stale/offline liveness**
- authenticated producer heartbeat + declared sensor capabilities
- **operator-owned sensor catalog metadata for UI/control surfaces**
- **restart-safe persistent sensor registry with atomic writes**
- **authenticated per-sensor desired-state control contract**
- **reference producer that physically closes capture while disabled**
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

## Operational status and control

`GET /api/v1/sensors/status` exposes runtime liveness, age, sequence,
accepted frames, producer drops, heartbeat count, capabilities and device
identity.

`GET /api/v1/sensors/catalog` joins runtime state with operator-owned metadata.
Metadata can be prepared before the sensor is online with
`PATCH /api/v1/sensors/{source_id}/metadata`.

Registry metadata is persisted by default to
`~/.visionrig/sensor-registry.json` using atomic replacement. Override with
`VISIONRIG_SENSOR_REGISTRY_FILE`; set it to an empty value for ephemeral mode.

A producer can fetch only its bounded desired state through
`GET /api/v1/sensors/{source_id}/desired-state`. Cross-device reads pass
through the authenticated gateway. V1 intentionally exposes only
`source_id` + `enabled`; friendly names, location, role, catalog and world
state are not disclosed to producers.

The reference producer now polls desired state before opening capture. When
`enabled=false`, an already-open webcam/screen source is closed, no frame
sequence is consumed, and only heartbeat/control polling continues. When
re-enabled, capture is reopened and resumes from the durable next sequence.

Remote producers can keep presence current independently of frame rate through
the authenticated gateway route `POST /api/v1/sensors/heartbeat`.

Raw pixels and embedding vectors do not enter Consciousness Core. All .mrvision
matches remain non-authoritative.

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
