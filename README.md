# VisionRig

VisionRig is Kaliv/ModelRig's dedicated visual-perception subsystem.

**Boundary:** VisionRig owns visual input -> structured perception. ModelRig owns
semantic interpretation, durable memory, cognition and Consciousness Core
world-state authority.

## Current state

VisionRig now includes:

- typed `PerceptionEvent/v2` observations
- local webcam/image/video capture
- bounded encoded camera/screen/VR ingress
- authenticated cross-device sensor gateway
- crash-safe webcam/screen reference producer
- optional YOLO ONNX detection + short-term tracking
- explicit detector label -> entity-kind mapping
- optional OCR, pose/hands/face landmarks and relative depth
- **bounded 2D spatial relations**
- bounded visual embedding sidecar
- encrypted, revisioned `.mrvision` profiles
- non-authoritative runtime recognition hints
- bounded cursor event journal for ModelRig
- verified model manifests with checksum/provenance/license metadata

## Perception flow

```text
frame
  |
  v
detector -> tracker -> OCR/landmarks/depth -> embeddings/recognition
                                              |
                                              v
                                      spatial relations
                                              |
                                              v
                                    PerceptionEvent v2
```

Spatial predicates currently express image-plane geometry only. VisionRig does
not infer holding, gaze or motion from insufficient evidence.

## Data path to cognition

```text
Kaliv / Windows / VR
       |
authenticated gateway / local capture
       |
       v
VisionRig perception
       |
       v
bounded event journal
       |
       v
ModelRig semantic bridge
       |
       v
Consciousness Core
```

Raw pixels and embedding vectors do not enter Consciousness Core. Recognition
hints remain non-authoritative.

See:
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/MODELS.md](docs/MODELS.md)
- [docs/MRVISION.md](docs/MRVISION.md)
- [docs/SPATIAL_SEMANTICS.md](docs/SPATIAL_SEMANTICS.md)
- [docs/SENSOR_INGRESS.md](docs/SENSOR_INGRESS.md)
- [docs/SENSOR_GATEWAY.md](docs/SENSOR_GATEWAY.md)
- [docs/PRODUCER.md](docs/PRODUCER.md)
