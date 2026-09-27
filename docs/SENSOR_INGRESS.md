# Sensor ingress

VisionRig can receive encoded frames and bounded multimodal sensor packets from
Kaliv clients, Windows capture producers and VR/passthrough producers without
giving those clients perception authority.

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
resulting `PerceptionEvent/v3` is available through the existing bounded
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


## Multimodal SensorPacket v1/v2

`POST /api/v1/sensor-packets/ingest` uses the same source metadata,
monotonic sequence checks, payload bound and single-slot overload policy as
encoded frame ingress.

Content-Type:

`application/vnd.visionrig.sensor-packet`

Both versions use the same bounded envelope shape:

```text
VRSP1\0 or VRSP2\0
uint32_be header_length
UTF-8 JSON header
encoded RGB bytes
optional depth bytes
optional infrared bytes
```

The header schema is `visionrig/sensor-packet/v1` or
`visionrig/sensor-packet/v2`. RGB remains JPEG, PNG or WebP and is never
recompressed by the packet layer.

v1 depth/IR planes are raw little-endian `uint16`. v2 adds per-plane
`compression` and `raw_byte_length`; the wire values remain `none` and
`zlib`. VisionRig 0.41.0 also provides an encoder-side `auto` strategy that
chooses independently per plane: zlib only when it is smaller, otherwise raw
`none`. `auto` is not a third wire compression type. Decompression is bounded
to the exact declared
`width*height*2` raw size, capped at 32 MiB per plane, and rejects trailing or
overlong compressed streams.

Depth values are millimeters and depth must be aligned to RGB/color coordinates.
A 0 depth sample means no valid metric reading.

VisionRig reconstructs the frame as:

```text
Frame.payload                  -> decoded RGB image
Frame.sensor_data.depth_mm     -> uint16 color-aligned depth plane
Frame.sensor_data.infrared     -> optional uint16 IR plane
Frame.sensor_data.metric_depth_sampler -> normalized RGB coord -> meters
```

The raw planes are frame-local and are not serialized into
`PerceptionEvent/v3`. Existing stages therefore remain camera-agnostic while
hardware-depth-aware stages can consume the metric sampler.

The authenticated gateway exposes only the fixed
`/api/v1/sensor-packets/ingest` route in addition to its existing allow-list.
It forwards the bounded body and whitelisted source query metadata to loopback
VisionRig and never becomes an arbitrary proxy.

SensorPacket v1/v2 intentionally does not define calibration transport or
perform depth alignment server-side. Producers must send a color-aligned depth plane.
This keeps Kinect/Quest vendor SDK objects outside the network contract and makes
the server-side sampler deterministic.


## Packet transport telemetry

For the most recently accepted SensorPacket on a source,
`GET /api/v1/sensors/status` includes
`packet_transport` with schema
`visionrig/sensor-packet-transport/v2`.

The telemetry contains only transport metadata:

- packet schema and total packet bytes;
- encoded RGB bytes;
- depth wire bytes, raw bytes and selected compression;
- infrared wire bytes, raw bytes and selected compression;
- combined numeric wire/raw byte totals and `numeric_saved_bytes`;
- `numeric_compression_ratio = numeric_wire_bytes / numeric_raw_bytes`;
- `payload_utilization = packet_bytes / configured_max_payload_bytes`;
- remaining `payload_headroom_bytes`;
- derived `payload_status`: `normal` below 0.80, `warning` from 0.80,
  and `critical` from 0.95.

No pixels, depth values, IR values or embeddings are exposed. The telemetry is
updated only after successful packet processing. A later successfully accepted
plain JPEG/PNG/WebP frame for the same source clears `packet_transport`, so
the status surface never presents old multimodal transport figures as current.


Warning and critical packet status is observability only. It does not change
desired sensor state, reject an otherwise valid packet, or grant new control
authority. The fleet summary surfaces warning/critical sources in its bounded
attention list with reason `packet_transport`.


### Explicit uncompressed v2

The encoder accepts an explicit v2 packet version with `compression=none`.
This produces SensorPacket/v2 framing and per-plane `compression=none` rather
than falling back to v1. It is used by negotiated producers when a peer supports
v2 plus raw numeric planes but does not advertise zlib.
