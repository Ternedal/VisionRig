# VisionRig

VisionRig is Kaliv/ModelRig's dedicated visual-perception subsystem.

**Boundary:** VisionRig owns visual input -> structured perception. ModelRig owns
semantic interpretation, durable memory, cognition and Consciousness Core
world-state authority.

## Current state — V2B

- `PerceptionEvent/v2`: entities, relations, typed pose/hand/face landmarks and relative depth
- bounded live-frame queue with explicit dropped-frame accounting
- optional OpenCV image/video/webcam sources
- checksum-verified YOLOv8-style ONNX object detection + short-term IoU tracking
- optional Tesseract OCR
- optional MediaPipe pose/hands/face landmarks
- optional generic ONNX monocular depth
- optional ONNX visual embeddings in a bounded **sidecar store** (vectors never enter PerceptionEvent)
- runtime probing for capture, OCR, landmarks, ONNX Runtime and CUDA
- no silent model downloads; YOLO/depth/embedding artifacts require verified manifests

## Core service

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m visionrig
```

The service listens on `http://127.0.0.1:8110`.

## Camera/video perception

Install the features you intend to run:

```powershell
pip install -e ".[capture,inference,ocr,landmarks,dev]"
```

Example:

```powershell
visionrig-capture --camera 0 \
  --model-manifest .\models\detector.json \
  --depth-manifest .\models\depth.json \
  --embedding-manifest .\models\embedding.json \
  --ocr --landmarks --verbose
```

Each ONNX artifact is admitted by a manifest that pins SHA-256, source and
artifact license. See [docs/MODELS.md](docs/MODELS.md).

## Data flow

```text
camera / screen / VR
        |
        v
 bounded capture queue
        |
        v
 detector -> tracker -> OCR -> landmarks -> depth
        |                              |
        |                              +--> transient embedding sidecar
        v
 PerceptionEvent v2
        |
        v
     ModelRig
 semantic/world integration
        |
        v
 Consciousness Core
```

Embeddings deliberately stay outside `PerceptionEvent`; future `.mrvision`
recognition can consume them without flooding cognition with vectors.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
