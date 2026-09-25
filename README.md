# VisionRig

VisionRig is Kaliv/ModelRig's dedicated visual-perception subsystem.

**Boundary:** VisionRig owns visual input -> structured perception. ModelRig owns
semantic interpretation, durable memory, cognition and Consciousness Core
world-state authority.

## Current state

VisionRig now includes:

- `PerceptionEvent/v2` typed observations
- local webcam/image/video capture
- bounded encoded sensor ingress for Kaliv camera/screen/VR
- **separate authenticated cross-device sensor gateway**
- optional YOLO ONNX detection + short-term tracking
- optional OCR, pose/hands/face landmarks and relative depth
- bounded visual embedding sidecar
- encrypted, revisioned `.mrvision` profiles
- non-authoritative runtime recognition hints
- bounded cursor event journal for ModelRig
- verified model manifests with checksum/provenance/license metadata

## Processes

Core service, loopback only:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[capture,dev]"
python -m visionrig
```

Optional cross-device ingress gateway:

```powershell
$env:VISIONRIG_GATEWAY_TOKEN="$(visionrig-gateway-token)"
$env:VISIONRIG_GATEWAY_BIND_HOST="<explicit interface IP>"
visionrig-gateway
```

The gateway exposes frame ingress only and forwards to the loopback core service.

## Data flow

```text
Kaliv / Windows / VR
       |
  local capture
       |
       +----------------------+
       |                      |
       | remote               v
       +------------> authenticated gateway
                              |
                              v
                    loopback sensor ingress
                              |
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

Raw pixels and embedding vectors do not enter Consciousness Core. Recognition
hints remain non-authoritative.

See:
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/MRVISION.md](docs/MRVISION.md)
- [docs/SENSOR_INGRESS.md](docs/SENSOR_INGRESS.md)
- [docs/SENSOR_GATEWAY.md](docs/SENSOR_GATEWAY.md)
