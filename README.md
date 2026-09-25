# VisionRig

VisionRig is Kaliv/ModelRig's dedicated visual-perception subsystem.

**Boundary:** VisionRig owns pixels -> structured perception. ModelRig owns semantic interpretation, durable memory, cognition, and Consciousness Core world-state authority.

## Current state

- strict, versioned perception contracts
- bounded live-frame queue with explicit dropped-frame accounting
- optional OpenCV image/video/webcam sources
- optional YOLOv8-style ONNX object detection
- short-term per-source IoU tracking
- checksum-verifiable model manifests with provenance/license metadata
- runtime capability probing for OpenCV, ONNX Runtime and CUDA
- FastAPI health/capabilities/world-state surface
- zero camera/CUDA dependency in the core package

## Core service

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m visionrig
```

The service listens on `http://127.0.0.1:8110`.

## Camera/video

Capture only:

```powershell
pip install -e ".[capture,dev]"
visionrig-capture --camera 0 --max-frames 100
```

Object detection + tracking:

```powershell
pip install -e ".[inference,dev]"
visionrig-capture --camera 0 --model-manifest .\models\detector.json --verbose
```

VisionRig deliberately does not download a detector automatically. See
[docs/MODELS.md](docs/MODELS.md) for the verified artifact contract.

## Runtime rule

`PerceptionEvent` objects are observations with provenance and confidence.
They are neither semantic truth nor permission to act.

```text
camera / screen / VR
        |
        v
 bounded capture queue
        |
        v
 detector -> tracker -> later OCR/pose/depth
        |
        v
 PerceptionEvent v1
        |
        v
     ModelRig
 semantic/world integration
        |
        v
 Consciousness Core
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
