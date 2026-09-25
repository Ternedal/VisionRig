# VisionRig

VisionRig is Kaliv/ModelRig's dedicated visual-perception subsystem.

**Boundary:** VisionRig owns visual input -> structured perception. ModelRig owns
semantic interpretation, durable memory, cognition and Consciousness Core
world-state authority.

## Current state — V3 + sensor ingress

- `PerceptionEvent/v2`: entities, relations, typed pose/hand/face landmarks and relative depth
- bounded live-frame transport with explicit dropped-frame accounting
- local OpenCV image/video/webcam sources
- **encoded sensor ingress for Kaliv camera/screen/VR producers**
- optional YOLOv8-style ONNX object detection
- short-term per-source IoU tracking
- optional Tesseract OCR
- optional MediaPipe pose/hands/face landmarks
- optional generic ONNX monocular depth
- optional ONNX visual embeddings in a bounded sidecar store
- encrypted revisioned `.mrvision` profiles and non-authoritative runtime recognition hints
- runtime probing for capture, OCR, landmarks, ONNX Runtime and CUDA
- no silent model downloads; ONNX artifacts require verified manifests

## Core service

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[capture,dev]"
python -m visionrig
```

The service listens on `http://127.0.0.1:8110`.

A production perception pipeline is configured through environment variables;
see [docs/SENSOR_INGRESS.md](docs/SENSOR_INGRESS.md).

## Data flow

```text
camera / screen / Kaliv VR
        |
        | local capture OR encoded frame ingress
        v
 detector -> tracker -> OCR -> landmarks -> depth
        |                              |
        |                              +--> transient embedding sidecar
        |                                         |
        |                                    .mrvision match
        v
 PerceptionEvent v2
        |
        v
 bounded event journal
        |
        v
     ModelRig
 semantic/world integration
        |
        v
 Consciousness Core
```

Recognition hints remain non-authoritative. Raw pixels and embedding vectors do
not enter Consciousness Core.

See:
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/MRVISION.md](docs/MRVISION.md)
- [docs/SENSOR_INGRESS.md](docs/SENSOR_INGRESS.md)
