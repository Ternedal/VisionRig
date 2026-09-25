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
$env:VISIONRIG_PRODUCER_STATE="D:\VisionRig\producer-state.json"
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

Native Android/Quest clients should implement the same durable state machine:

1. atomically reserve/increment a sequence before sending each captured frame;
2. persist which sequence is in-flight;
3. on 200, validate receipt and atomically clear pending drops + in-flight;
4. on 429/network miss, atomically clear in-flight and increment pending drops;
5. after process restart, convert any leftover in-flight frame into one drop;
6. on 401/403 or malformed receipt, fail visibly;
7. never treat a VisionRig recognition hint as identity authority.

This makes the Python producer an executable reference for Kotlin/Quest clients.
