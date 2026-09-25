# VisionRig observability

VisionRig exposes operational telemetry separately from semantic perception.
This keeps health/UI concerns out of PerceptionEvent while making sensor
failures, overload and stale producers visible to ModelRig and Kaliv.

## Sensor status endpoint

`GET /api/v1/sensors/status`

Schema: `visionrig/sensor-runtime-status/v1`

The snapshot includes:

- `accepted_total`
- rejection counters for busy ingress, sequence violations, payload size,
  unsupported media types and decode failures
- `active_processing`
- one entry per accepted remote producer with source id/type, device,
  last accepted sequence, accepted frame count, accumulated producer-reported
  drops and `last_seen_utc`

Only operational metadata is exposed. Raw images, depth arrays and embeddings
are not returned.

## Health integration

`GET /health` uses `visionrig/health/v5` and embeds the same runtime snapshot
under `sensor_ingress.runtime`. Existing queue, downstream sink and ModelRig
bridge status remain present.

## UI semantics

A control surface should treat the endpoint as telemetry, not identity or
world-state authority. A producer is known only after at least one accepted
frame. Sequence regressions remain rejected with HTTP 409, overload with 429,
oversized frames with 413, unsupported media types with 415 and decode failures
with 422.

The counters are process-local and reset when the VisionRig service restarts.
Durable history belongs outside VisionRig.
