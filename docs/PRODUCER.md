# VisionRig reference producer

The reference producer demonstrates the client side of the sensor protocol and is
usable directly on Windows/Linux for webcam or desktop-screen input.

## Install

```powershell
pip install -e ".[producer]"
```

The producer extra installs OpenCV, NumPy and MSS.

## Configure

Do not put the gateway token on the command line; command-line arguments can be
visible to other local processes.

```powershell
$env:VISIONRIG_GATEWAY_URL="http://<Tailscale-IP>:8111"
$env:VISIONRIG_PRODUCER_TOKEN="<same gateway token>"
```

Webcam:

```powershell
visionrig-producer --camera 0 --source-id windows-webcam --fps 5 --verbose
```

Desktop:

```powershell
visionrig-producer --screen 1 --source-id windows-screen --fps 3 --verbose
```

One image:

```powershell
visionrig-producer --image .\test.jpg --source-id test-image
```

## Backpressure semantics

A frame sequence represents **capture order**, not successful-delivery order.

If sequence 11 is rejected with HTTP 429, sequence 11 is not retried. The next
capture is sequence 12 and carries `dropped_frames=1`. Multiple consecutive
drops accumulate until the next accepted frame.

```text
capture seq 10 -> 200
capture seq 11 -> 429        (drop 1)
capture seq 12 -> 429        (drop 2)
capture seq 13 -> 200, dropped_frames=2
```

Network-unavailable frames follow the same freshness rule. Authentication or
protocol errors are different: the reference producer stops fail-closed instead
of silently burning frames forever.

## Client contract for Kaliv/Quest

Native Android/Quest clients do not need this Python package. They should
implement the same small contract:

1. increment the local sequence for every captured frame;
2. send JPEG/PNG/WebP with bearer authentication;
3. on 200, validate receipt source + sequence and clear accumulated drops;
4. on 429, do not retry the frame; increment accumulated drops;
5. on a network miss, drop the frame and continue with fresh imagery;
6. on 401/403 or malformed receipt, stop/fail visibly;
7. never treat a VisionRig recognition hint as identity authority.

This makes the Python producer an executable reference implementation for the
Kotlin/Quest producers.
