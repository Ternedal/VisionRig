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
- **reference webcam/screen producer with correct drop/sequence semantics**
- optional YOLO ONNX detection + short-term tracking
- optional OCR, pose/hands/face landmarks and relative depth
- bounded visual embedding sidecar
- encrypted, revisioned `.mrvision` profiles
- non-authoritative runtime recognition hints
- bounded cursor event journal for ModelRig
- verified model manifests with checksum/provenance/license metadata

## Processes

Core perception service:

```powershell
python -m visionrig
```

Cross-device gateway:

```powershell
$env:VISIONRIG_GATEWAY_TOKEN="$(visionrig-gateway-token)"
$env:VISIONRIG_GATEWAY_BIND_HOST="<explicit Tailscale/interface IP>"
visionrig-gateway
```

Reference webcam/screen producer:

```powershell
pip install -e ".[producer]"
$env:VISIONRIG_GATEWAY_URL="http://<Tailscale-IP>:8111"
$env:VISIONRIG_PRODUCER_TOKEN="<gateway token>"
visionrig-producer --screen 1 --source-id windows-screen --fps 3
```

## Data flow

```text
Kaliv / Windows / VR
       |
       +-- native producer
       |        |
       |        v
       |   authenticated gateway
       |        |
       |        v
       +--> loopback sensor ingress
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
- [docs/PRODUCER.md](docs/PRODUCER.md)
