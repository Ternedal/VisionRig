# Kaliv Quest passthrough producer

Native Meta Quest passthrough camera adapter for VisionRig.

Supported target:
- Quest 3 / Quest 3S;
- Horizon OS v74+;
- Android Camera2 passthrough camera API;
- both `android.permission.CAMERA` and `horizonos.permission.HEADSET_CAMERA`;
- passthrough feature enabled.

The adapter discovers Meta passthrough cameras using the official vendor tags:
- `com.meta.extra_metadata.camera_source`;
- `com.meta.extra_metadata.position`.

It selects a supported YUV_420_888 output size from the active passthrough camera
instead of assuming a fixed headset resolution, encodes the latest bounded frame
as JPEG, and feeds the shared Kaliv producer state machine. Applications may
request an exact even width/height pair when needed; unsupported pairs fail closed. Desired-state disable closes
the camera. Control-plane, permission, camera, session and transport failures
fail closed.

Quest 2 is intentionally not declared supported because Meta's Passthrough
Camera Access API exposes forward-facing RGB frames only on Quest 3 / Quest 3S.

The app remains responsible for requesting runtime permissions before running
the producer session.


## Network transport

The library declares Android `INTERNET` permission but deliberately does not force
`android:usesCleartextTraffic="true"`. If the app connects to the current
`http://<tailscale-ip>:8111` VisionRig gateway, the consuming application must
explicitly allow that cleartext transport through its own Android network-security
policy. Prefer HTTPS if/when TLS termination is added to the gateway. Do not widen
cleartext access globally by accident.


## Threading

`KalivQuestProducerSession.step()` is blocking producer work and must run on a worker thread/coroutine dispatcher, never the Android main/UI thread. The Quest Camera2 adapter fails closed if `open()` or `capture()` is invoked on the main thread.


## Physical qualification

Once the Quest producer is online in VisionRig as source `kaliv-quest`, qualify
the physical passthrough path with the generic VisionRig physical gate:

```powershell
$sha = (git -C C:\path\to\VisionRig rev-parse HEAD).Trim()
visionrig-qualify-physical --expected-sha $sha --source-id kaliv-quest
```

Create an obvious semantic scene change in the headset camera view while the
gate is running. A PASS requires a fresh `vr` PerceptionEvent/v4 from that exact
source, a fresh exact-bound ModelRig admission, stable VisionRig process/build
identity through finalization, and no production/identity/memory authority
escalation.

The qualifier runs against the loopback VisionRig service. The Quest 3/3S client
must already be online and actively delivering passthrough frames through the
authenticated sensor gateway.
