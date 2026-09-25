# VisionRig

VisionRig is Kaliv/ModelRig's dedicated visual-perception subsystem.

**Boundary:** VisionRig owns pixels -> structured perception. ModelRig owns semantic interpretation, durable memory, cognition, and Consciousness Core world-state authority.

## Current state — V1 capture foundation

- strict, versioned perception contracts
- pluggable perception stages
- bounded live-frame queue with explicit dropped-frame accounting
- ephemeral visual world snapshot
- FastAPI service with health, capabilities, ingest, queue stats and world state
- optional OpenCV image/video/webcam sources
- runtime capability probing for OpenCV, ONNX Runtime and CUDA provider presence
- zero camera/CUDA dependency in the core package

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m visionrig
```

For local camera/video capture support:

```powershell
pip install -e ".[capture,dev]"
visionrig-capture --camera 0 --max-frames 100
```

The service listens on `http://127.0.0.1:8110`.

## Runtime rule

VisionRig emits `PerceptionEvent` observations. They are evidence with provenance
and confidence — not semantic truth and not permission to act.

```text
camera / screen / VR
        |
        v
 bounded capture queue
        |
        v
    VisionRig
  perception stages
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
