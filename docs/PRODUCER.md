# VisionRig reference producer

The reference producer demonstrates the client side of the sensor protocol and is
usable directly on Windows/Linux for webcam or desktop-screen input.

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

The producer polls desired state every two seconds by default. Override with
`--control-poll-seconds` between 0.25 and 60 seconds.

## Remote pause/resume semantics

The reference producer checks
`GET /api/v1/sensors/{source_id}/desired-state` **before opening capture**.

When `enabled=false`:

- webcam/screen/image capture is not opened, or an existing source is closed;
- no frame is captured or encoded;
- no frame sequence is reserved/consumed;
- heartbeat continues on each control poll so VisionRig can still report the
  producer as online;
- the producer keeps polling until it sees `enabled=true`.

When re-enabled, the capture source is reopened and sending resumes at the
durable next sequence.

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
3. on enable, open capture and atomically reserve/increment a sequence before
   sending each captured frame;
5. persist which sequence is in-flight;
6. on 200, validate receipt and atomically clear pending drops + in-flight;
7. on 429/network miss, atomically clear in-flight and increment pending drops;
8. after process restart, convert any leftover in-flight frame into one drop;
9. on 401/403, invalid desired state or malformed receipts, fail visibly and
   close capture;
10. never treat a VisionRig recognition hint as identity authority.

This makes the Python producer an executable reference for Kotlin/Quest clients.
