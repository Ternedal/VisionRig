# VisionRig sensor gateway

The VisionRig core service stays on loopback. Cross-device clients use a
separate, deliberately small gateway process that exposes only encoded frame
ingress.

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
proxy those routes. It accepts only:

`POST /api/v1/frames/ingest`

and forwards it to the fixed loopback core endpoint.

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

## Client request

```text
POST /api/v1/frames/ingest?source_id=kaliv-vr&source_type=vr&frame_sequence=42
Authorization: Bearer <token>
Content-Type: image/jpeg

<encoded frame>
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
- only frame ingress is exposed;
- replayed/stale frames are rejected downstream by per-source sequence checks;
- no cognition, memory, identity or execution authority is added.

For the user's existing Tailscale setup, binding the gateway to the machine's
Tailscale interface keeps transport inside Tailscale's encrypted network while
the bearer token supplies application-level admission.

Do not expose this gateway directly to the public internet. If that becomes a
requirement later, add explicit TLS/mTLS termination and a stronger device
identity/enrollment layer rather than widening this contract.
