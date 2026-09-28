# Physical perception qualification

`visionrig-qualify-physical` is the physical release-evidence gate for the
VisionRig → ModelRig perception boundary.

It does not synthesize a camera event and it does not upload a test image. A
PASS requires a **fresh event from an already-online physical `camera` or
`vr` sensor source** after the qualifier starts.

## Prerequisites

VisionRig must be running on loopback with its ModelRig semantic bridge enabled:

~~~text
VISIONRIG_MODELRIG_BRIDGE=1
VISIONRIG_MODELRIG_WORKER_URL=http://127.0.0.1:8099
~~~

ModelRig must separately have the already-reviewed receiving surface and live
Consciousness session available:

~~~text
KALIV_CONSCIOUSNESS_VISIONRIG_ENABLED=1
~~~

A physical producer must be online and have already delivered at least one
frame. The gate deliberately rejects `screen` and `image` sources as proof
of physical vision.

## Run

~~~powershell
visionrig-qualify-physical --source-id kinect-v2-0
~~~

or let the qualifier select the freshest online camera/VR source:

~~~powershell
visionrig-qualify-physical
~~~

Default VisionRig origin:

~~~text
http://127.0.0.1:8110
~~~

The output receipt is written atomically to:

~~~text
validation/visionrig-physical-perception-latest.json
~~~

## What the gate proves

A PASS requires all of the following on one bounded observation:

1. VisionRig health is `ok` and advertises `PerceptionEvent/v3`.
2. The ModelRig bridge is enabled and points explicitly to loopback.
3. The selected source is online and its type is `camera` or `vr`.
4. The source had accepted real frames before qualification.
5. A new journal event appears after the qualifier's starting cursor.
6. That event belongs to the exact selected source and has a frame sequence
   newer than the preflight sequence.
7. VisionRig exposes a privacy-safe bridge result for that exact source/frame.
8. The bridge result is `published` or `replayed` and includes the
   ModelRig receipt VisionRig already validated.
9. The receipt's `visionrig_event_ref` equals SHA-256 of the exact serialized
   journal event and `observed_sequence` equals the frame sequence.
10. ModelRig reports `epistemic_status=inferred`, zero model calls, no
    SelfState write, no durable-memory/execution/scheduling authority and
    `production_activation=false`.
11. The source accepted-frame counter advanced during qualification.

The operator should create an obvious semantic scene change while the gate is
running (for example enter/leave frame or present/remove a visible object).
Normal frame-to-frame jitter is intentionally suppressed by the semantic bridge
and is not sufficient evidence.

## Privacy boundary

The report contains source identity, frame sequence, event/evidence references
and timing only. It explicitly does **not** persist:

- raw RGB/IR/depth data;
- OCR text;
- entity labels;
- landmarks;
- the semantic PerceptionEvent payload.

The small `modelrig_bridge.last_result` health projection follows the same
rule: it contains only source/frame identity and the already-verified ModelRig
receipt fields.

## Authority

This gate measures a physical perception path. It grants no sensor, identity,
memory, action or production authority. A PASS is suitable evidence for the
cross-repository `visionrig_physical_perception` release gate; it is not
permission to activate production by itself.
