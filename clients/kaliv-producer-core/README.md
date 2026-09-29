# Kaliv native producer core

Kotlin/JVM reference core for Kaliv Android and Quest producers.

Implemented in this slice:
- authenticated VisionRig gateway client;
- strict desired-state, heartbeat and frame-receipt validation;
- crash-safe durable sequence/drop state compatible with the Python producer semantics;
- fail-closed desired-state control loop;
- hardware-agnostic encoded-frame capture interface reusable by Android camera and Quest passthrough adapters;
- Kotlin/JVM CI tests.

Not implemented here:
- Android CameraX binding;
- Meta Quest passthrough/camera binding;
- lifecycle/service UI;
- SensorPacket/v2 depth/IR transport.

Those are platform adapter slices on top of this core, not alternate protocol implementations.


## Durable state identity

Native producer state is keyed by the normalized VisionRig gateway URL, `sourceId`
and `sourceType`. Switching gateways or source types therefore does not reuse an
unrelated sequence/drop journal.

The state file provides crash/restart integrity through atomic replacement. It is
not a multi-process leader-election mechanism: run only one active producer for
a given state identity at a time.


## ProducerRunner

`ProducerRunner` drives a `ProducerLoop` with bounded cadence:
- enabled sources use configured FPS cadence;
- disabled sources back off to a slower control poll;
- captured frames count even when delivery is dropped;
- no extra terminal delay is added after the configured frame limit;
- loop cleanup runs from `finally` and cannot mask a primary runner failure or cancellation.
