# Sensor ingress

VisionRig can receive encoded frames from Kaliv clients, Windows capture
producers and VR/passthrough producers without giving those clients perception
authority.

## Endpoint

`POST /api/v1/frames/ingest`

Query metadata:

- `source_id`: stable producer/source identifier;
- `source_type`: `camera`, `screen`, `vr` or `image`;
- `frame_sequence`: monotonically increasing per source;
- optional `device`;
- optional `dropped_frames` from the producer.

The request body is one encoded JPEG, PNG or WebP frame.

Example:

```text
POST /api/v1/frames/ingest?source_id=kaliv-vr-left&source_type=vr&frame_sequence=42
Content-Type: image/jpeg

<encoded JPEG bytes>
```

A successful response is a `visionrig/sensor-frame-receipt/v1`, and the
resulting `PerceptionEvent/v2` is available through the existing bounded
journal.

## Backpressure policy

Remote input is deliberately **not** an unbounded request queue.

VisionRig has one inference admission slot for encoded remote frames. If it is
busy, another producer receives HTTP 429 and should drop that stale frame rather
than retrying it later.

That gives the producer a simple rule:

```text
capture newest frame
       |
       v
POST to VisionRig
       |
       +-- 200 -> advance sequence
       |
       +-- 429 -> drop this frame; continue with newer frame
```

This preserves freshness. A visual system that is five seconds behind reality is
worse than one that explicitly dropped intermediate frames.

## Limits and failure modes

- default frame limit: 8 MiB;
- configurable up to 64 MiB through `VISIONRIG_MAX_SENSOR_FRAME_BYTES`;
- stale/duplicate source sequence: HTTP 409;
- unsupported media type: HTTP 415;
- undecodable image: HTTP 422;
- overloaded inference slot: HTTP 429;
- over-size frame: HTTP 413.

The service reads request bodies with an explicit byte limit and rejects an
oversized declared `Content-Length` before streaming the body.

## Real service pipeline

`python -m visionrig` now composes the service pipeline from environment
configuration:

```text
VISIONRIG_YOLO_MANIFEST
VISIONRIG_DEPTH_MANIFEST
VISIONRIG_EMBEDDING_MANIFEST
VISIONRIG_OCR=0|1
VISIONRIG_LANDMARKS=0|1
VISIONRIG_FORCE_CPU=0|1
VISIONRIG_MAX_SENSOR_FRAME_BYTES
```

Model manifests retain the existing checksum/provenance rules. No models are
silently downloaded.

The service remains bound to `127.0.0.1:8110` by default. Cross-device Kaliv
transport should go through an explicitly designed authenticated boundary rather
than making this raw service listen broadly.
