# VisionRig observability

VisionRig separates three concepts that a control surface must not blur:

1. **runtime sensor state** — what VisionRig has actually seen;
2. **producer declaration** — capabilities/device reported by the producer;
3. **operator metadata** — names, placement, role and desired enable state.

None of these is perception identity authority.

## Sensor runtime status

`GET /api/v1/sensors/status`

Schema: `visionrig/sensor-runtime-status/v2`.

Per source it exposes source/device identity, declared capabilities, last
sequence, accepted frames, producer-reported drops, heartbeat count,
`last_seen_utc`, `age_seconds` and a derived presence state:

- `online`: seen within 15 seconds by default;
- `stale`: not fresh, but seen within 60 seconds by default;
- `offline`: older than 60 seconds.

An accepted frame refreshes presence, so high-rate producers do not require
separate heartbeats.

## Heartbeat

`POST /api/v1/sensors/heartbeat` accepts
`visionrig/sensor-heartbeat/v1` with source id/type, optional device and a
bounded capability list. Cross-device clients use the same route through the
authenticated sensor gateway.

Typical capabilities include `rgb`, `depth`, `infrared`, `screen` and
`passthrough`. They are descriptive telemetry only.

## Operator sensor catalog

`PATCH /api/v1/sensors/{source_id}/metadata` stores operator-owned metadata:

- `display_name`
- `location`
- `role`: `ambient`, `primary`, `tracking`, `screen`, `vr` or `other`
- `enabled`: desired control-plane state

`GET /api/v1/sensors/catalog` joins registry metadata with current runtime
state. This allows a UI to preconfigure a sensor that has not connected yet and
keeps offline sensors visible.

The registry is restart-safe by default. Service startup loads
`VISIONRIG_SENSOR_REGISTRY_FILE`, defaulting to
`~/.visionrig/sensor-registry.json`. Updates are written using fsync plus
atomic file replacement. Invalid/corrupt registry content fails closed rather
than being silently discarded. Set the environment value to empty for
explicitly ephemeral operation.

The `enabled` value is **not enforced by ingress** in this slice. It is an
explicit operator intention for a later actuator/control contract.

## Health integration

`GET /health` uses `visionrig/health/v7`, embeds sensor runtime status and
reports the number of configured registry entries.

Only operational metadata is exposed. Raw images, depth arrays and embeddings
are never returned by these surfaces.
