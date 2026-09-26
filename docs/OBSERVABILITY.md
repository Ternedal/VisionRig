# VisionRig observability

VisionRig exposes operational telemetry separately from semantic perception.
This keeps health/UI concerns out of PerceptionEvent while making sensor
failures, overload, availability and stale producers visible to ModelRig/Kaliv.

## Sensor status endpoint

`GET /api/v1/sensors/status`

Schema: `visionrig/sensor-runtime-status/v2`.

Per source it exposes source/device identity, declared capabilities, last
sequence, accepted frames, producer-reported drops, heartbeat count,
`last_seen_utc`, `age_seconds` and a derived presence state:

- `online`: seen within 15 seconds by default;
- `stale`: not fresh, but seen within 60 seconds by default;
- `offline`: older than 60 seconds.

A successfully accepted frame also refreshes presence, so high-rate producers do
not require separate heartbeats.

## Heartbeat

`POST /api/v1/sensors/heartbeat` accepts
`visionrig/sensor-heartbeat/v1` with source id/type, optional device and a
bounded capability list. It returns
`visionrig/sensor-heartbeat-receipt/v1`.

Cross-device clients use the same route through the authenticated sensor
gateway. The gateway remains allow-list based: only frame ingress and heartbeat
are proxied to the loopback core service.

Typical capabilities include `rgb`, `depth`, `infrared`, `screen` and
`passthrough`. They are descriptive telemetry only and do not grant authority.

## Health integration

`GET /health` uses `visionrig/health/v6` and embeds the same runtime snapshot
under `sensor_ingress.runtime`.

Only operational metadata is exposed. Raw images, depth arrays and embeddings
are never returned by the status/heartbeat surfaces. Counters and presence
history are process-local; durable history belongs outside VisionRig.
