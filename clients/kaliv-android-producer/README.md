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



For continuous capture, prefer the shared coroutine runner instead of manually
reimplementing polling/FPS cadence:

```kotlin
lifecycleScope.launch(Dispatchers.IO) {
    producer.run(
        config = ProducerRunConfig(
            fps = 5.0,
            disabledPollMillis = 2_000,
        ),
    )
}
```

Cancellation closes capture through the shared runner `finally` path. The same
session can be run again later; desired-state is re-read before capture reopens.

The app remains responsible for:
- requesting CAMERA permission before creating/running the producer;
- choosing scheduling/lifecycle cadence for `step()`;
- keeping the bearer token in an appropriate secret store;
- never exposing the VisionRig gateway directly to the public internet.

`create()` currently builds its gateway client with the default `OkHttpClient()`
(follows redirects) and does not accept an injected client; see issue #181 and
the [core README](../kaliv-producer-core/README.md#known-limitation-redirects-issue-181).

The module deliberately does not own UI, identity, cognition, memory or action authority.


## Build and test

The library uses Android Gradle Plugin 9.5.1 and Kotlin 2.4.21, with
`compileSdk = 37`, `minSdk = 26` and Java 17 source/target compatibility. The
shared core is pulled in as a composite build from `../kaliv-producer-core`.
There is no Gradle wrapper; CI uses Temurin JDK 17, Gradle 9.6.0 and the Android
SDK packages below, run from the repository root:

```bash
sdkmanager "platforms;android-37.0" "build-tools;36.0.0"
gradle -p clients/kaliv-android-producer :library:assembleDebug :library:testDebugUnitTest --no-daemon
```


## Network transport

The library declares Android `INTERNET` permission but deliberately does not force
`android:usesCleartextTraffic="true"`. If the app connects to the current
`http://<tailscale-ip>:8111` VisionRig gateway, the consuming application must
explicitly allow that cleartext transport through its own Android network-security
policy. Prefer HTTPS if/when TLS termination is added to the gateway. Do not widen
cleartext access globally by accident.


## Threading

`KalivAndroidProducerSession.step()` is a blocking producer step and must run on a worker thread/coroutine dispatcher, never the Android main/UI thread. The CameraX adapter fails closed if `open()` or `capture()` is invoked on the main thread. Lifecycle binding itself is marshalled back to the main executor internally.


## Physical qualification

Once the Android producer is online in VisionRig as source `kaliv-android`, use
the same physical release-evidence gate as Kinect:

```powershell
$sha = (git -C C:\path\to\VisionRig rev-parse HEAD).Trim()
visionrig-qualify-physical --expected-sha $sha --source-id kaliv-android
```

While the gate is running, create an obvious semantic scene change in front of
the phone camera. A PASS requires a fresh `camera` PerceptionEvent/v4 from that
exact source, a fresh exact-bound ModelRig admission, stable VisionRig
process/build identity through finalization, and no production/identity/memory
authority escalation.

The qualifier talks to the loopback VisionRig service, not directly to the
Android device. The Android producer must already be online and actively
delivering frames through the authenticated sensor gateway.
