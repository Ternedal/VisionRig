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

Native Kaliv clients use the same gate once they are online and already
delivering frames through the authenticated sensor gateway:

~~~powershell
# Kaliv Android / CameraX
$sha = (git rev-parse HEAD).Trim()
visionrig-qualify-physical --expected-sha $sha --source-id kaliv-android

# Kaliv Quest 3/3S / passthrough Camera2
$sha = (git rev-parse HEAD).Trim()
visionrig-qualify-physical --expected-sha $sha --source-id kaliv-quest
~~~

For Android the source type is `camera`; for Quest it is `vr`. In both cases
the qualifier still runs locally against the loopback VisionRig service. The
remote device is evidence only after VisionRig has accepted fresh frames from
that exact source.

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

1. VisionRig health is `ok` and advertises `PerceptionEvent/v4`.
2. The ModelRig bridge is enabled and points explicitly to loopback.
3. The selected source is online and its type is `camera` or `vr`.
4. The source had accepted real frames before qualification.
5. A new journal event appears after the qualifier's starting cursor.
6. That event belongs to the exact selected source and has a frame sequence
   newer than the preflight sequence.
7. VisionRig exposes a privacy-safe bridge result for that exact source/frame.
8. The bridge result is a fresh `published` admission and includes the
   ModelRig receipt VisionRig already validated; replay cannot satisfy the gate.
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

For **release** evidence, start VisionRig with `VISIONRIG_GIT_SHA` set to the
exact checkout revision and run:

~~~powershell
$sha = (git rev-parse HEAD).Trim()
visionrig-qualify-physical --expected-sha $sha --source-id kinect-v2-0
~~~

When the PASS report is bound to a validated 40-hex `service_revision`, it
also contains one canonical content-addressed reference:

~~~text
visionrig-physical-perception:<visionrig-sha>:<sha256>
~~~

That exact reference is the only VisionRig physical-perception evidence format
accepted by ModelRig's system release gate. A PASS report without a validated
service revision deliberately has **no** `release_evidence_ref` and cannot be
promoted to release authority.


## Stable bridge evidence window

VisionRig retains a bounded in-memory history of the latest 128 successful
`published`/`replayed` ModelRig bridge receipt bindings. Suppressed,
unavailable, or rejected later frames do not erase an already verified
source/frame binding before the physical qualifier can observe it.

The retained entries contain only the same privacy-safe source/frame identity
and verified receipt authority/provenance fields exposed by health. Raw frames,
OCR text, landmarks, labels, and perception semantics are not retained by this
history.


## Qualification receipt v2

VisionRig 0.90.0 advances the generic physical qualification receipt to `visionrig/physical-perception-qualification/v2`. The receipt now records only privacy-safe semantic evidence metadata for the exact qualifying event: total observation count, bounded observation categories (`entities`, `relations`, `landmarks`, `depth`, `infrared`, `scene`), and per-category counts. It never records entity labels, OCR text, scene text, landmark coordinates, depth values, or infrared measurements. The existing exact event hash remains the authoritative binding to the full event.


## WorldState-qualified release evidence

VisionRig 0.91.0 requires the exact-bound ModelRig admission receipt used by generic physical qualification to report `world_changed=true`. A successful transport/admission with no WorldState transition is therefore insufficient for the release gate. This aligns the generic camera/VR qualification semantics with the dedicated Kinect physical acceptance path.


## Fresh-admission requirement

VisionRig 0.92.0 requires the generic physical qualification gate to bind to a fresh `published` ModelRig admission. A `replayed` bridge result, or a receipt whose `replayed` flag is true, cannot satisfy release evidence even if it references the same event. This prevents a prior idempotent admission from being reused as proof of a new physical perception run.


## Stable physical source identity

VisionRig 0.94.0 binds generic physical qualification to the same physical source identity across preflight, the qualifying `PerceptionEvent/v4`, and the final sensor-status sample. A matching `source_id` is no longer enough: `source_type` and device identity must remain stable for the full run. Device/source replacement during qualification therefore fails closed. The final sensor snapshot must also report `last_sequence` at or beyond the exact qualifying event sequence, so the status evidence cannot lag behind the event that made the gate pass.


## Build identity binding

VisionRig 0.96.0 exposes `service_version` and optional validated `service_revision` in health v67. Set `VISIONRIG_GIT_SHA` to the exact 40-hex checkout revision when starting the service. `visionrig-qualify-physical --expected-sha <sha>` then fails closed unless the running service reports that exact revision, and the PASS receipt records both version and revision for later audit.


## Cognition admission proof

VisionRig 0.97.0 strengthens generic physical qualification after fresh ModelRig WorldState admission. A PASS now also requires the ModelRig receipt to expose a canonical `world-evidence-event:<64hex>` evidence reference, `cognition_event_queued=true`, and a canonical `cevt-<32hex>` cognition event id. This matches ModelRig's atomic fresh-admission contract: a non-replay world change queues exactly one typed cognition event, while replay does not.


## Final VisionRig identity binding

Physical qualification now re-reads VisionRig health during finalization after
the qualifying source/event/ModelRig binding has been established. The PASS
receipt is rejected if `service_instance_id`, `service_version`, or
`service_revision` differs from preflight.

This closes the final restart/build-drift window between bridge admission and
the final sensor-status sample. A release PASS therefore belongs to one stable
VisionRig process/build identity for the complete qualification run.
