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


## ProducerRunner

`ProducerRunner` drives a `ProducerLoop` with bounded cadence:
- enabled sources use `fps` cadence;
- disabled sources back off to `disabledPollMillis`;
- captured frames count even when delivery is dropped, matching the Python reference producer's capture-order semantics;
- the underlying loop is always closed from `finally` on completion, failure or coroutine cancellation.

Applications may still call `step()` directly when their own lifecycle scheduler owns cadence.
