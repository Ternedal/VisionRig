# VisionRig observability and sensor control

VisionRig separates four concepts that a control surface must not blur:

1. **runtime sensor state** — what VisionRig has actually seen;
2. **producer declaration** — capabilities/device reported by the producer;
3. **operator metadata** — names, placement, role and desired enable state;
4. **producer desired state** — the minimal command surface a producer may read.

None of these is perception identity authority.

## Sensor runtime status

`GET /api/v1/sensors/status`

Schema: `visionrig/sensor-runtime-status/v3`.

Per source it exposes source/device identity, declared capabilities, last
sequence, accepted frames, producer-reported drops, heartbeat count,
`last_seen_utc`, `age_seconds`, `capture_active` and derived `online/stale/offline` presence.

## Heartbeat

`POST /api/v1/sensors/heartbeat` accepts
`visionrig/sensor-heartbeat/v1`. Cross-device clients use the same route
through the authenticated sensor gateway.

## Operator sensor catalog

`PATCH /api/v1/sensors/{source_id}/metadata` stores operator-owned metadata:
`display_name`, `location`, `role` and `enabled`.

`GET /api/v1/sensors/catalog` joins registry metadata with current runtime
state. The registry is restart-safe by default at
`~/.visionrig/sensor-registry.json` and uses fsync plus atomic replacement.

## Desired-state control contract

`GET /api/v1/sensors/{source_id}/desired-state` returns
`visionrig/sensor-desired-state/v1`:

- `source_id`
- `enabled`
- `production_authority=false`

The authenticated cross-device gateway exposes the same exact GET route and
forwards it only to the loopback core service. It does not expose the sensor
catalog, world state, journal, profiles, models or other control-plane data.

Unknown/unconfigured sensor ids default to `enabled=true`, matching registry
metadata defaults. Operators can preconfigure `enabled=false` before a device
ever connects.

The reference producer validates schema + source id through
`fetch_desired_state()`. This slice provides the transport/contract; automatic
capture-loop pausing can be layered on top without giving the producer broader
authority.

## Health integration

`GET /health` uses `visionrig/health/v9` and advertises the desired-state
schema under the sensor registry section.

Only operational/control metadata is exposed. Raw images, depth arrays and
embeddings are never returned by these surfaces.
