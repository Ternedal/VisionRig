# VisionRig

VisionRig is Kaliv/ModelRig's dedicated visual-perception subsystem.

**Boundary:** VisionRig owns visual input -> structured perception. ModelRig owns
semantic interpretation, durable memory, cognition and Consciousness Core
world-state authority.

## Current state

VisionRig includes:

- typed PerceptionEvent/v2 observations
- local webcam/image/video capture\n- Kinect v2 RGB + hardware depth + infrared capture
- bounded camera/screen/VR ingress
- authenticated cross-device sensor gateway
- crash-safe webcam/screen reference producer
- optional YOLO ONNX detection + short-term tracking
- explicit detector label -> entity-kind mapping
- OCR, pose/hands/face landmarks and relative depth adapters
- bounded 2D spatial relations
- bounded full-frame/entity embedding sidecar
- encrypted, revisioned .mrvision profiles
- **service-loaded face/body/object/place recognition hints**
- bounded cursor event journal for ModelRig
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
PerceptionEvent v2
```

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
- docs/PRODUCER.md\n- docs/KINECT_V2.md
