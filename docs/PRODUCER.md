# VisionRig reference producer

The reference producer demonstrates the client side of the sensor protocol and is
usable directly on Windows/Linux for webcam, desktop-screen, image and Kinect v2
input.

## Install

```powershell
pip install -e ".[producer]"
```

## Configure

Do not put the gateway token on the command line.

```powershell
$env:VISIONRIG_GATEWAY_URL="http://<Tailscale-IP>:8111"
$env:VISIONRIG_PRODUCER_TOKEN="<same gateway token>"
```

Optional producer-state location:

```powershell
$env:VISIONRIG_PRODUCER_STATE="D:\\VisionRig\\producer-state.json"
```

Default: `~/.visionrig/producer-state.json`.

Webcam:

```powershell
visionrig-producer --camera 0 --source-id windows-webcam --fps 5 --verbose
```

Desktop:

```powershell
visionrig-producer --screen 1 --source-id windows-screen --fps 3 --verbose
```

Remote Kinect v2:

```powershell
pip install -e ".[producer,kinect-v2]"
visionrig-producer --kinect-v2 --source-id kinect-living-room --fps 5 --verbose
```

Kinect mode JPEG-encodes RGB, color-aligns the hardware depth plane on the
producer machine using the Kinect mapper, and sends RGB + aligned uint16 depth +
uint16 IR as SensorPacket/v2 with adaptive per-plane compression. Each numeric
plane is zlib-compressed only when the compressed bytes are smaller than the raw
uint16 bytes; otherwise that plane is sent uncompressed. RGB remains JPEG. It
uses the same persistent sequence/drop state,
desired-state polling and heartbeat acknowledgement as the webcam producer.

### Kinect packet budget adaptation

Remote Kinect mode applies a producer-side SensorPacket budget before calling
`send_packet()`. The default is 8 MiB, matching the default core/gateway
limit. Override it with:

```powershell
$env:VISIONRIG_PRODUCER_MAX_PACKET_BYTES="8388608"
# or:
visionrig-producer --kinect-v2 --max-packet-bytes 8388608
```

The producer starts at `--jpeg-quality` (default 80). If the full packet is at
or above the 80% payload-warning threshold, it re-encodes only the RGB JPEG in
5-quality-point steps down to `--min-jpeg-quality` (default 30). Depth and IR
planes are never downsampled, quantized or discarded by this adaptation.

If the minimum-quality packet is still below the hard packet budget, it is sent
even if its utilization remains warning/critical because the numeric planes may
dominate the packet. If it exceeds the hard budget, the producer raises a visible
error **before** calling `send_packet()`; no durable sequence is reserved and no
hidden drop is created.

For deployments where core or gateway limits differ from the default, set the
producer budget to the lower effective limit.

The producer polls desired state every two seconds by default. Override with
`--control-poll-seconds` between 0.25 and 60 seconds.

## Remote pause/resume semantics

The reference producer checks
`GET /api/v1/sensors/{source_id}/desired-state` **before opening capture**. The response includes a monotonically increasing control `revision`.

When `enabled=false`:

- webcam/screen/image/Kinect capture is not opened, or an existing source is closed;
- no frame is captured or encoded;
- no frame sequence is reserved/consumed;
- heartbeat continues on each control poll so VisionRig can still report the
  producer as online;
- the producer keeps polling until it sees `enabled=true`.

When re-enabled, the capture source is reopened and sending resumes at the
durable next sequence. After applying either enabled or disabled state, the
producer reports both `capture_active` and `applied_revision`. VisionRig only
reports convergence when the acknowledged revision matches the current command.

Desired-state lookup is fail-closed in the reference loop. If the control plane
cannot be read or returns an invalid/mismatched response, the producer exits and
the `finally` path closes any open source instead of continuing unsupervised
capture.

## Backpressure and crash semantics

A frame sequence represents **capture order**, not successful-delivery order.

```text
capture seq 10 -> 200
capture seq 11 -> 429        (drop 1)
capture seq 12 -> 429        (drop 2)
capture seq 13 -> 200, dropped_frames=2
```

The reference producer persists three pieces of state before/after network I/O:

- next source sequence;
- accumulated dropped frames;
- currently in-flight sequence.

Reservation is written **before** sending a frame. If the process or machine dies
with an in-flight frame, the next process startup converts that unfinished frame
into one explicit drop and continues with the next sequence.

This prevents a producer restart from resetting to sequence 0 while VisionRig is
still alive, and prevents a crash from silently hiding a lost visual frame.

The file is updated through atomic replacement. Run only one active producer for
a given gateway/source/type key; cross-process leader election is intentionally
not part of the reference client.

## Client contract for Kaliv/Quest

Native Android/Quest clients should implement the same control + durable state
machine:

1. authenticate and fetch desired state before opening camera/passthrough;
2. while disabled, keep capture closed and publish heartbeat while polling;
3. apply desired state and acknowledge that exact `revision` only after the
   hardware/capture state reflects it;
4. on enable, open capture and atomically reserve/increment a sequence before
   sending each captured frame;
5. persist which sequence is in-flight;
6. on 200, validate receipt and atomically clear pending drops + in-flight;
7. on 429/network miss, atomically clear in-flight and increment pending drops;
8. after process restart, convert any leftover in-flight frame into one drop;
9. on 401/403, invalid desired state or malformed receipts, fail visibly and
   close capture;
10. never treat a VisionRig recognition hint as identity authority.

This makes the Python producer an executable reference for Kotlin/Quest clients.


## Transport capability negotiation

VisionRig 0.45.0 removes the reference Kinect producer's assumption that the
remote gateway/core accept the same payload size as the local default.

Before opening Kinect capture, the producer authenticates to:

`GET /api/v1/producer-capabilities`

The gateway queries only loopback core `/health`, extracts the bounded sensor
ingress transport fields, and returns
`visionrig/producer-capabilities/v2` with:

- `max_payload_bytes = min(gateway_max_payload_bytes, core_max_payload_bytes)`;
- the individual gateway/core limits for diagnostics;
- supported SensorPacket schemas;
- supported packet compression modes;
- packet payload warning and critical utilization thresholds.

The Kinect producer requires SensorPacket/v2 and chooses:

`effective_packet_budget = min(local_max_packet_bytes, negotiated_max_payload_bytes)`

The local `--max-packet-bytes` /
`VISIONRIG_PRODUCER_MAX_PACKET_BYTES` value is therefore an upper cap, not a
claim about the remote service. Negotiation is fail-closed for remote Kinect:
invalid credentials, unavailable core health, malformed capability responses or
missing SensorPacket/v2 support stop before capture opens.


## Live capability refresh

VisionRig 0.46.0 refreshes the authenticated producer capability contract while
remote Kinect capture is running. The default refresh interval is 30 seconds:

```powershell
$env:VISIONRIG_PRODUCER_CAPABILITY_REFRESH_SECONDS="30"
# or:
visionrig-producer --kinect-v2 --capability-refresh-seconds 30
```

The accepted range is 1..3600 seconds. Each refresh recomputes:

`effective_packet_budget = min(local_max_packet_bytes, negotiated_max_payload_bytes)`

before the next frame is encoded. A lower gateway/core limit therefore changes
the very next packet budget without reopening the source and without consuming
or resetting the durable frame sequence.

Capability refresh remains fail-closed. If the authenticated route is
unavailable, malformed, unauthorized, or loses required SensorPacket/v2
support, the error propagates through the existing controlled-capture
`finally` path and closes the capture source. The producer does not continue
indefinitely using a stale remembered limit.


## Negotiation heartbeat telemetry

VisionRig 0.47.0 extends producer heartbeat to
`visionrig/sensor-heartbeat/v3`. Remote Kinect publishes the effective
negotiated packet budget together with the UTC timestamp of the most recent
successful capability refresh and the configured refresh interval.

The tuple is sent on the normal control heartbeat after a successful
negotiation/refresh. Core runtime status can therefore distinguish a currently
refreshed transport contract from a stale one without trusting the age of the
last video frame.

Heartbeat v2 remains accepted for older producers. V2 has no negotiation
telemetry and appears as `capability_refresh_status=unknown`. V3 negotiation
fields are all-or-nothing; partial tuples are rejected rather than interpreted.


## Negotiated packet compression

VisionRig 0.49.0 makes the advertised SensorPacket compression capabilities
operational rather than informational.

Remote Kinect selects its v2 plane encoder from the latest negotiated capability
contract:

- `none + zlib` -> adaptive `auto` per plane;
- `zlib` only -> force zlib for depth/IR;
- `none` only -> send raw uint16 depth/IR in SensorPacket/v2;
- no known mode -> fail closed before capture/send.

Raw v2 is explicit: the producer keeps the SensorPacket/v2 magic/schema even
when both numeric planes are uncompressed. This avoids silently downgrading to
SensorPacket/v1.

The selection is refreshed together with the negotiated payload ceiling. A
running producer can therefore move between adaptive, forced-zlib and raw-v2
encoding between frames without reopening Kinect, changing frame sequence state,
or touching depth/IR semantics.


## Clock-skew-safe negotiation freshness

VisionRig 0.50.0 separates the producer-reported refresh timestamp from the
freshness clock used by core.

Heartbeat v3 still carries `capability_refreshed_utc` as the producer's
refresh token/diagnostic timestamp. When VisionRig first observes a new token it
records its own `capability_refresh_observed_utc`. Runtime freshness age and
`current/stale` classification use only that server-observed timestamp.

Repeated heartbeats with the same producer refresh token do not reset age. A
genuinely new refresh token resets the server-observed age to zero. Producer
wall-clock skew can therefore no longer create false stale/current states,
while both timestamps remain visible for diagnostics.


## Heartbeat v4 compression telemetry

VisionRig 0.51.0 extends producer negotiation observability with heartbeat v4.
When the active negotiated packet transport plan has a concrete compression
strategy, the reference producer sends:

- `negotiated_max_payload_bytes`;
- `capability_refreshed_utc`;
- `capability_refresh_seconds`;
- `negotiated_packet_compression` (`none`, `zlib` or encoder-side
  `auto`).

Budget-only compatibility paths continue to use heartbeat v3. Core accepts v2,
v3 and v4. A change in compression strategy emits one semantic
`runtime_changed` event; timestamp-only capability refreshes remain quiet.


## Negotiated packet-pressure target

VisionRig 0.52.0 makes the server-advertised packet pressure thresholds part of
the producer capability contract. The gateway returns
`producer-capabilities/v2` with
`packet_payload_warning_utilization` and
`packet_payload_critical_utilization` copied from the loopback core's bounded
sensor-ingress health contract.

The reference Kinect producer uses the negotiated **warning** utilization as the
target passed to adaptive JPEG budgeting. For example, if the core changes its
warning threshold from 0.80 to 0.70, the next capability refresh causes RGB
quality adaptation to target below 70% of the effective byte ceiling. Metric
depth and IR are unchanged.

The producer still accepts legacy `producer-capabilities/v1` responses for
compatibility. Since v1 did not carry thresholds, that path falls back to the
historical 0.80 warning target. V2 threshold fields are all required and must
satisfy `0 < warning < critical <= 1`; malformed policy is fail-closed.


## Heartbeat v5 target policy telemetry

VisionRig 0.53.0 publishes the producer's active packet-size target through
heartbeat v5 as `negotiated_packet_target_utilization`. For the reference
Kinect producer this is the target selected from the negotiated server warning
threshold and used by RGB JPEG adaptation.

Heartbeat v5 is selected only when a target-utilization value is present.
Heartbeat v4 remains valid for negotiated compression without target telemetry,
and older v3/v2 contracts remain accepted by the core.

The target is observability only. It does not change server-side desired state
or grant the producer any new authority.


## Heartbeat v6 observed packet utilization

VisionRig 0.54.0 extends producer telemetry with
`observed_packet_utilization`. For adaptive Kinect SensorPacket capture this is
the most recent encoded packet size divided by the negotiated payload ceiling.

The producer continues to report
`negotiated_packet_target_utilization` as policy and now reports
`observed_packet_utilization` as measurement. This makes it possible to see
whether JPEG adaptation is actually operating near the requested budget without
turning normal packet-size variation into semantic control events.


## Heartbeat contract transition events

VisionRig 0.85.0 emits one semantic `runtime_changed` event when a runtime
producer changes accepted heartbeat schema version. The event includes the new
`heartbeat_schema_id`.

The transition is detected atomically around the heartbeat mutation, so
concurrent duplicate upgrade heartbeats cannot create duplicate contract-change
events. Repeated heartbeats on the same schema and telemetry-only refreshes stay
silent.


## Native Kaliv producer core

The first native client slice now lives in `clients/kaliv-producer-core`. It is a Kotlin/JVM library shared by future Kaliv Android and Quest capture adapters. It implements the same fail-closed desired-state polling, authenticated heartbeat/frame transport and crash-safe sequence/drop recovery semantics as the Python reference producer. The platform-specific camera/passthrough adapters remain separate follow-up slices.


## Kaliv Android CameraX adapter

The native Android adapter lives under `clients/kaliv-android-producer`. It uses CameraX `ImageCapture` and the shared Kotlin producer core, so desired-state convergence, heartbeat acknowledgement and durable sequence/drop semantics stay aligned with the JVM core. Capture fails closed on missing permission, CameraX lifecycle/binding failure or VisionRig control/transport failure. Default durable state is isolated by gateway URL, source id and source type.
