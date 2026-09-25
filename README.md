# VisionRig

VisionRig is Kaliv/ModelRig's dedicated visual-perception subsystem.

**Boundary:** VisionRig owns pixels -> structured perception. ModelRig owns semantic interpretation, durable memory, cognition, and Consciousness Core world-state authority.

## Current foundation

- strict, versioned perception contracts
- pluggable frame sources and perception stages
- bounded in-memory world snapshot
- FastAPI service with health, ingest and world-state endpoints
- synthetic source for deterministic development/tests
- zero camera/CUDA dependency in the core package

The first slice is deliberately model-agnostic. OpenCV, ONNX Runtime, YOLO/RT-DETR, face/body embeddings, OCR and depth can be added as optional adapters without contaminating the core contract.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m visionrig
```

Then open `http://127.0.0.1:8110/docs`.

## Contract

VisionRig emits `PerceptionEvent` objects. Consumers should treat them as observations, not truth:

```text
camera / screen / VR
        |
        v
    VisionRig
  capture + perception
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
