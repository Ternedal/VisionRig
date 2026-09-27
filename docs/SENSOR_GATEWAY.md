# VisionRig sensor gateway

The VisionRig core service stays on loopback. Cross-device clients use a
separate, deliberately small gateway process that exposes only the explicit
sensor transport/control routes needed by remote producers.

```text
Kaliv Android / Kaliv VR / remote screen producer
                    |
             Bearer token
                    |
                    v
        VisionRig Sensor Gateway
        explicit interface :8111
                    |
           loopback forwarding
                    |
                    v
       VisionRig core 127.0.0.1:8110
                    |
                    v
             perception
```

## Why a separate process

Binding the core VisionRig API to LAN/Tailscale would also expose health details,
event journal, world snapshot and other service routes. The gateway does not
proxy those routes. It accepts only this fixed allow-list:

- `POST /api/v1/frames/ingest`;
- `POST /api/v1/sensor-packets/ingest`;
- `POST /api/v1/sensors/heartbeat`;
- `GET /api/v1/sensors/{source_id}/desired-state`;
- `GET /api/v1/producer-capabilities`.

Each route forwards only to its matching fixed loopback core endpoint. There is
no generic path proxy.

## Start

Generate a token once:

```powershell
visionrig-gateway-token
```

Set the token and an **explicit interface address**:

```powershell
$env:VISIONRIG_GATEWAY_TOKEN="<generated token>"
$env:VISIONRIG_GATEWAY_BIND_HOST="<your Tailscale interface IP>"
visionrig-gateway
```

Default gateway port: `8111`.

The gateway intentionally rejects wildcard binds such as `0.0.0.0` and `::`.
Bind the exact Tailscale/LAN interface that should receive sensor traffic.

## Client requests

Encoded RGB:

```text
POST /api/v1/frames/ingest?source_id=kaliv-vr&source_type=vr&frame_sequence=42
Authorization: Bearer <token>
Content-Type: image/jpeg

<encoded frame>
```

Multimodal RGB + optional depth/IR:

```text
POST /api/v1/sensor-packets/ingest?source_id=kinect-living-room&source_type=camera&frame_sequence=42
Authorization: Bearer <token>
Content-Type: application/vnd.visionrig.sensor-packet

<SensorPacket/v1 or v2 bytes>
```

The gateway never forwards the client's Authorization header to VisionRig core.
It reconstructs a small allowlisted query set and forwards only the encoded body
and Content-Type.

## Security invariants

- bearer token is mandatory and must be at least 32 characters;
- token comparison uses constant-time comparison;
- target VisionRig endpoint must resolve to loopback by configuration;
- credentials in the target URL are rejected;
- wildcard bind addresses are rejected;
- payload size is bounded before forwarding;
- upstream redirects are not followed;
- only the documented frame, sensor-packet, heartbeat and per-source
  desired-state routes are exposed;
- replayed/stale frames are rejected downstream by per-source sequence checks;
- no cognition, memory, identity or execution authority is added.

For the user's existing Tailscale setup, binding the gateway to the machine's
Tailscale interface keeps transport inside Tailscale's encrypted network while
the bearer token supplies application-level admission.

Do not expose this gateway directly to the public internet. If that becomes a
requirement later, add explicit TLS/mTLS termination and a stronger device
identity/enrollment layer rather than widening this contract.


## Producer capability negotiation

`GET /api/v1/producer-capabilities` is bearer-authenticated and returns only
bounded transport information needed before remote capture starts. The gateway
reads the loopback core health response and extracts the sensor-ingress maximum
payload plus supported SensorPacket schemas/compressions. It does not proxy the
full core health response.

The effective advertised payload ceiling is the lower of the gateway and core
limits. If core health is unavailable, malformed, or lacks the expected bounded
transport fields, the gateway returns 502 rather than guessing.
