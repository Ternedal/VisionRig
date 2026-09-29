# Kaliv Android CameraX producer

Android CameraX adapter for the shared VisionRig Kaliv producer core.

This module:
- binds CameraX `ImageCapture` to the app lifecycle;
- captures JPEG frames through the shared `EncodedCapture` contract;
- keeps capture closed until VisionRig desired-state enables the source;
- sends heartbeat acknowledgement before each enabled capture step;
- persists sequence/drop state under app files;
- fails closed on permission, lifecycle, control-plane or transport errors.

Typical app integration:

```kotlin
val producer = KalivAndroidProducerSession.create(
    context = applicationContext,
    lifecycleOwner = this,
    gatewayUrl = "http://<tailscale-ip>:8111",
    token = visionRigToken,
    sourceId = "kaliv-android",
)

val result = producer.step()
```

The app remains responsible for:
- requesting CAMERA permission before creating/running the producer;
- choosing scheduling/lifecycle cadence for `step()`;
- keeping the bearer token in an appropriate secret store;
- never exposing the VisionRig gateway directly to the public internet.

The module deliberately does not own UI, identity, cognition, memory or action authority.


## Network transport

The library declares Android `INTERNET` permission but deliberately does not force
`android:usesCleartextTraffic="true"`. If the app connects to the current
`http://<tailscale-ip>:8111` VisionRig gateway, the consuming application must
explicitly allow that cleartext transport through its own Android network-security
policy. Prefer HTTPS if/when TLS termination is added to the gateway. Do not widen
cleartext access globally by accident.


## Threading

`KalivAndroidProducerSession.step()` is a blocking producer step and must run on a worker thread/coroutine dispatcher, never the Android main/UI thread. The CameraX adapter fails closed if `open()` or `capture()` is invoked on the main thread. Lifecycle binding itself is marshalled back to the main executor internally.
