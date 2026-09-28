# VisionRig

VisionRig is Kaliv/ModelRig's dedicated visual-perception subsystem.

**Boundary:** VisionRig owns visual input -> structured perception. ModelRig owns
semantic interpretation, durable memory, cognition and Consciousness Core
world-state authority.

## Current state

VisionRig includes:

- typed PerceptionEvent/v3 observations
- local webcam/image/video capture
- Kinect v2 RGB + hardware depth + infrared capture
- **managed local webcam/Kinect capture through the shared desired/effective-state contract**
- bounded camera/screen/VR ingress
- authenticated cross-device sensor gateway
- **bounded binary SensorPacket v1/v2 transport for RGB + color-aligned metric depth + infrared**
- **per-source SensorPacket transport telemetry with compression ratio and ingress-limit utilization**
- **fleet attention for SensorPacket payload pressure with warning/critical thresholds**
- **remote Kinect v2 producer with producer-side color/depth alignment**
- **producer-side Kinect packet budgeting with adaptive RGB JPEG quality**
- **authenticated gateway/core transport capability negotiation for remote producers**
- **live capability refresh so remote Kinect adapts to payload-limit changes without restart**
- **negotiated SensorPacket compression strategy with live raw/zlib fallback**
- **negotiated packet-pressure thresholds so adaptive JPEG follows server policy**
- **heartbeat v3/v4/v5 observability for negotiated producer budget, compression, target utilization and refresh freshness**
- **fleet attention for stale producer capability negotiation**
- **fleet packet-target compliance with target-vs-observed attention**
- **clock-skew-safe capability freshness using VisionRig-observed refresh time**
- crash-safe webcam/screen reference producer
- **live sensor/runtime observability with online/stale/offline liveness**
- authenticated producer heartbeat + declared sensor capabilities
- **operator-owned sensor catalog metadata for UI/control surfaces**
- **restart-safe persistent sensor registry with atomic writes**
- **automatic persistent registration of first-seen sensors**
- **persistent discovered sensor type/device/capabilities with stable source identity**
- **persistent first-seen/last-seen timestamps and observation count**
- **authenticated per-sensor desired-state control contract**
- **reference producer that physically closes capture while disabled**
- **closed-loop desired/effective sensor control convergence in the catalog**
- **revisioned sensor commands with explicit producer acknowledgement**
- **persistent command timestamps and measurable pending control age**
- **reversible sensor retirement that preserves discovery/history**
- **guarded permanent forget for retired/offline sensors**
- **bounded sensor fleet summary for UI and health surfaces**
- **bounded cursor-based semantic sensor change feed for incremental UI updates**
- **race-safe UI bootstrap snapshot with catalog, fleet and change cursor**
- **restart-detectable sensor change streams with explicit stream identity**
- **bounded long-poll sensor changes with event wake-up and timeout fallback**
- **restart-safe persistent semantic sensor change journal with atomic writes**
- **persistent semantic sensor state revision for change-feed drift detection**
- **optimistic concurrency guards for operator sensor writes**
- **server-side registry/change-journal consistency status with persistent high-water tracking**
- optional YOLO ONNX detection + short-term tracking
- explicit detector label -> entity-kind mapping
- OCR, pose/hands/face landmarks, relative depth and metric hardware depth
- bounded 2D relations plus sensor-backed front/behind depth ordering
- bounded full-frame/entity embedding sidecar
- encrypted, revisioned .mrvision profiles + image enrollment/revocation CLI
- **service-loaded face/body/object/place recognition hints**
- bounded cursor event journal for ModelRig
- opt-in semantic PerceptionEvent/v3 -> Consciousness Core bridge with change suppression
- verified model manifests with checksum/provenance/license metadata

## Operational status and control

`GET /api/v1/sensors/status` exposes runtime liveness, age, sequence,
accepted frames, producer drops, heartbeat count, capabilities and device
identity.

`GET /api/v1/sensors/catalog` joins runtime state with operator-owned metadata.
Metadata can be prepared before the sensor is online with
`PATCH /api/v1/sensors/{source_id}/metadata`. A previously unknown source is
automatically persisted on its first valid heartbeat or frame, so it remains
visible in the catalog after restart even before an operator names it. Discovery data
(type, device and capabilities) is persisted separately from operator metadata,
so an offline sensor is still identifiable after restart.

Registry metadata is persisted by default to
`~/.visionrig/sensor-registry.json` using atomic replacement. Override with
`VISIONRIG_SENSOR_REGISTRY_FILE`; set it to an empty value for ephemeral mode.

Operators can retire a sensor with
`POST /api/v1/sensors/{source_id}/retire`. Retirement preserves metadata and
discovery history, records `retired_utc`, and drives desired state to disabled
through the normal revisioned control path. `restore` removes the retirement
marker but deliberately leaves the sensor disabled until it is explicitly
enabled again. Permanent removal uses
`DELETE /api/v1/sensors/{source_id}` and is accepted only when the sensor is
both retired and offline. Forget removes metadata, discovery and control history
plus process-local ingress sequence/runtime state; a later observation is a new
registration.

A producer can fetch only its bounded desired state through
`GET /api/v1/sensors/{source_id}/desired-state`. Cross-device reads pass
through the authenticated gateway. V2 intentionally exposes only
`source_id` + `enabled` + `revision`; friendly names, location, role, catalog and world
state are not disclosed to producers.

The reference producer now polls desired state before opening capture. When
`enabled=false`, an already-open webcam/screen source is closed, no frame
sequence is consumed, and only heartbeat/control polling continues. When
re-enabled, capture is reopened and resumes from the durable next sequence.

Heartbeat reports the actually applied `capture_active` state plus the
`applied_revision`. The sensor catalog compares both with the current
`enabled` + desired revision and reports `converged`, `pending` or
`unknown`. An old heartbeat can therefore never make a newer command look
applied. Each changed command also persists `desired_changed_utc`; while it is
not converged the catalog exposes neutral `pending_seconds` telemetry.

`GET /api/v1/sensors/fleet` provides a compact operational summary for UI and
monitoring: lifecycle counts, online/stale/offline/unknown presence, control
convergence counts and a bounded attention list. The same payload is embedded in
`/health`; it is observability only and grants no new control authority.

`GET /api/v1/sensors/changes` provides a separate process-local cursor feed for
semantic sensor changes such as registration, discovery, metadata/control,
retire/restore/forget and producer applied-state changes. Repeated heartbeats
that only refresh liveness do not create events. Time-based transitions from
online to stale/offline remain derived state in fleet/status and therefore still
require periodic liveness polling.

`GET /api/v1/sensors/bootstrap` gives UI clients their initial catalog + fleet
state together with a safe `change_cursor` and `change_stream_id`. The cursor
is sampled before the snapshot is built, so a concurrent change may be replayed
once through the change feed but cannot be silently missed. Clients send the
stream id back to `/changes`; after a service restart the new process returns
`stream_reset=true` instead of silently accepting a cursor from the old
journal. Clients may add `wait_seconds` (0..30) to hold an empty change
request until a semantic event arrives or the timeout expires; gap and
stream-reset responses never wait.

The service process persists the bounded semantic journal by default at
`~/.visionrig/sensor-changes.json` using atomic replacement. That preserves
stream id, cursors and retained events across a normal restart, so connected UI
clients can continue without a reset. Override with
`VISIONRIG_SENSOR_CHANGE_JOURNAL_FILE`; set it to an empty value for
process-local/ephemeral mode.

The sensor registry also persists a monotonic semantic `state_revision`.
Bootstrap and fleet expose the current revision, and every semantic sensor-change
event is stamped with the registry revision it reflects. Heartbeat-only
last-seen/counter refreshes do not advance it. If a client observes a fleet or
bootstrap revision newer than the highest semantic event revision it has
applied, it can detect a registry/journal crash-window gap and rebootstrap
instead of trusting an incomplete incremental history.

Operator mutations support an optional `expected_state_revision` query
parameter on metadata patch, retire, restore and permanent forget. A stale
revision is rejected with HTTP 409 and a structured
`sensor_state_revision_conflict` detail instead of overwriting newer state.
Successful writes return the resulting `state_revision`. Existing clients may
omit the precondition, but control UIs should send the revision from their
latest bootstrap/fleet state.

The change journal persists a `state_revision_high_water`. Fleet, bootstrap and
health compare it with the registry revision and expose
`synced`, `registry_ahead` or `journal_ahead`. This turns the cross-file
crash window into explicit operational state instead of requiring each client
to infer it from event history.

Remote producers can keep presence current independently of frame rate through
the authenticated gateway route `POST /api/v1/sensors/heartbeat`.

Multimodal producers can use
`POST /api/v1/sensor-packets/ingest` with
`application/vnd.visionrig.sensor-packet`. SensorPacket/v1 carries encoded RGB
plus optional raw little-endian uint16 depth/IR planes. SensorPacket/v2 keeps the
same frame semantics but can zlib-compress depth/IR independently of RGB, with
bounded decompression and explicit raw-length validation.
Depth is required to be color-aligned and is exposed frame-locally as both
`depth_mm` and a metric sampler; raw planes still do not cross into
PerceptionEvent/Consciousness Core. `visionrig-producer --kinect-v2` performs Kinect color/depth alignment on the
producer host and emits SensorPacket/v2 with adaptive per-plane compression:
zlib is used only when it makes that depth/IR plane smaller; otherwise the plane
stays raw. The packet still uses the same durable sequence/backpressure state
machine as normal frames. The reference Kinect producer also keeps a local
packet budget (8 MiB by default) and lowers only RGB JPEG quality in 5-point
steps until the packet is below the 80% warning threshold where possible.
Depth/IR are never degraded. If the packet still exceeds the hard local budget
at minimum JPEG quality, it fails before sequence reservation/send.

Before Kinect capture opens, the reference producer now requests
`GET /api/v1/producer-capabilities` through the authenticated gateway. The
gateway reads only the loopback core health transport section and returns the
effective payload ceiling as `min(gateway_limit, core_limit)` plus supported
SensorPacket schemas/compressions. The producer then uses
`min(local_cap, negotiated_cap)` as its packet budget and fails closed if the
transport contract cannot be negotiated. During capture the same bounded
transport contract is refreshed every 30 seconds by default; if the effective
remote ceiling changes, the next packet uses the new budget without resetting
the durable frame sequence. A failed refresh stops capture through the existing
fail-closed cleanup path rather than continuing on stale transport assumptions.
The same capability contract now controls the actual SensorPacket v2
compression strategy as well: `auto` when both raw and zlib are advertised,
forced `zlib` when only zlib is available, and raw v2 when only `none` is
available. A live capability refresh can change that encoder strategy between
frames without reopening capture or resetting the durable sequence.

Heartbeat v3 reports the currently applied negotiated packet ceiling, the
producer-reported refresh timestamp and refresh interval. Heartbeat v4 extends
that contract with the producer's selected SensorPacket compression strategy
(`none`, `zlib` or encoder-side `auto`). VisionRig also records
the local UTC time when it first observes each new refresh timestamp. Runtime
freshness age/status is derived from that **server-observed** time, so producer
clock skew cannot make a fresh negotiation look stale or keep an old negotiation
fresh. Both timestamps remain visible for diagnostics without persisting that
transport telemetry into operator registry state. Fleet v6 counts those states
and adds only `stale` producers to bounded attention with reason
`capability_refresh`; legacy/unknown heartbeat-v2 producers are not treated as
faults. Timestamp-only refreshes do not create change-feed events; an actual
negotiated budget or compression-strategy change does.

`GET /api/v1/sensors/status` exposes the most recent accepted SensorPacket
transport telemetry per source: total packet bytes, RGB bytes, raw/wire
depth+IR bytes, per-plane compression, numeric compression ratio, saved bytes,
headroom and fraction of the configured ingress byte limit. Payload utilization
below 80% is `normal`, 80% through <95% is `warning`, and 95%+ is
`critical`. Warning/critical sources enter the bounded fleet attention list
with reason `packet_transport`; VisionRig does not automatically disable them.
A later accepted non-packet RGB frame clears that telemetry so dashboards do not
display stale transport data.

Raw pixels and embedding vectors do not enter Consciousness Core. All .mrvision
matches remain non-authoritative.

See:
- docs/ARCHITECTURE.md
- docs/MODELS.md
- docs/MRVISION.md
- docs/MRVISION_RUNTIME.md
- docs/SPATIAL_SEMANTICS.md
- docs/SENSOR_INGRESS.md
- docs/SENSOR_GATEWAY.md
- docs/PRODUCER.md
- docs/KINECT_V2.md
- docs/PERCEPTION_V3.md
- docs/MODELRIG_BRIDGE.md
- docs/OBSERVABILITY.md


### Negotiated packet-pressure policy

VisionRig 0.52.0 extends the authenticated producer capability contract to
`visionrig/producer-capabilities/v2`. In addition to the effective byte
ceiling and supported SensorPacket modes, the gateway now forwards the core's
current packet warning/critical utilization thresholds.

The remote Kinect producer uses the negotiated warning threshold as the adaptive
RGB JPEG target. A server policy change therefore takes effect at the next
capability refresh without changing depth/IR semantics or restarting capture.
Older v1 capability responses remain parseable and fall back to the historical
80% warning target.


Heartbeat v5 extends transient producer negotiation observability with
`negotiated_packet_target_utilization`. This is the actual packet budget
fraction the producer applies when adapting RGB JPEG quality. It is exposed in
runtime/catalog/fleet surfaces and only emits `runtime_changed` when the target
policy itself changes; ordinary capability refresh timestamps remain quiet.


## VisionRig 0.54.0: observed packet utilization

Heartbeat v6 adds `observed_packet_utilization` alongside the negotiated packet
target. The reference Kinect producer reports the latest achieved SensorPacket
utilization, and the gateway/core expose it through runtime status v11. The
measurement is intentionally transient and does not generate semantic
`runtime_changed` events.


## VisionRig 0.55.0: fleet packet-target compliance

Fleet summary v7 aggregates heartbeat v6 target-vs-observed packet utilization
as `within_target`, `above_target` or `unknown`. Producers above their
negotiated packet target enter the bounded attention list with
`reason = packet_target`, including both values for diagnostics. The signal
is observational only and does not alter capture policy or semantic state.


## VisionRig 0.56.0: sustained packet-target attention

Packet-target compliance now distinguishes a transient oversized packet from a
sustained producer-side adaptation problem. Runtime status v12 tracks consecutive
over-target heartbeat-v6 observations, while fleet summary v8 raises
`packet_target` attention only after three consecutive exceedances. A compliant
or unknown measurement resets the streak immediately.


## VisionRig 0.57.0: packet-target pressure duration

Runtime status v13 now exposes when a consecutive over-target run started, how
many seconds it has remained active, and when an over-target measurement was
last observed. Fleet summary v9 classifies target pressure as `clear`,
`transient`, `sustained` or `unknown`; only sustained pressure enters the
existing bounded attention list. This keeps alerting quiet while giving control
surfaces enough temporal context to distinguish spikes from persistent producer
adaptation problems.


## VisionRig 0.58.0: sustained pressure episodes

Runtime status v14 now counts sustained packet-target pressure episodes and
records the latest recovery time after a sustained run. The episode counter
increments exactly once when an over-target streak first reaches the shared
three-heartbeat sustained threshold; remaining above target does not inflate the
count. Recovery is recorded only when a sustained run returns to a compliant or
unknown measurement.

Fleet summary v10 exposes the aggregate sustained-episode count and how many
sources have recovered from at least one sustained episode. Attention entries
also include each source's episode count and last recovery timestamp.


## VisionRig 0.59.0: pressure recurrence telemetry

Runtime status v15 now tracks whether sustained packet-target pressure returns
after recovery. `packet_target_recurrence_count` increments only when a new
sustained episode starts after a recorded sustained recovery, while
`packet_target_last_recurrence_seconds` records the server-clock interval from
that recovery to the recurrence threshold crossing.

Fleet summary v11 aggregates total recurrences and the number of sources that
have recurred at least once. This distinguishes a sensor with one historical
incident from a sensor whose transport adaptation repeatedly falls back into
sustained pressure.


## VisionRig 0.60.0: packet-target stability classification

Fleet summary v12 adds a configurable recurrence stability view. The default
`packet_target_flap_window_seconds` is 120 seconds and can be overridden when
constructing the service.

Each known/runtime source is classified as:

- `stable`: no sustained recurrence has been observed;
- `flapping`: the latest sustained recurrence crossed the threshold within the
  configured flap window after recovery;
- `recurring`: sustained pressure returned, but outside the flap window;
- `unknown`: no runtime source is available.

This is descriptive transport observability only. It does not change desired
capture state or producer authority.


## VisionRig 0.61.0: catalog packet-target stability

The sensor catalog now exposes the packet-target stability projection directly
on every source. Catalog v9 adds a compact `packet_target` block containing the
current stability classification, effective flap window, recurrence count and
latest recurrence interval.

This lets control surfaces render per-sensor `stable`, `recurring`,
`flapping` or `unknown` state from the catalog/bootstrap snapshot without
joining fleet attention data back onto catalog rows.


## VisionRig 0.62.0: complete catalog packet-target diagnostics

Catalog v10 expands each source's `packet_target` projection from stability
alone into a UI-ready diagnostic snapshot. It now includes latest target and
observed utilization, derived compliance and pressure state, debounce streak and
timing, sustained episode/recovery history, recurrence telemetry and the
effective attention/flap thresholds.

Fleet and catalog share the same compliance, pressure and stability classifiers,
so operator surfaces can render one consistent per-sensor transport state
without reconstructing policy client-side.


## VisionRig 0.63.0: packet-target overshoot telemetry

Packet-target diagnostics now quantify how far the latest observed packet is
above its negotiated target instead of exposing only a binary compliance state.

Per-source catalog diagnostics and fleet attention expose
`overshoot_delta` / `packet_target_overshoot_delta` as
`max(0, observed - target)`, plus an overshoot ratio relative to the target.
A compliant measured source reports `0.0`; unavailable target/observed data
reports `null`.

Fleet summary v13 also exposes a bounded `packet_target_overshoot` aggregate
with the number of measurable sources and the current maximum delta and ratio.
This remains diagnostic telemetry only and does not alter capture or adaptation
policy.


## VisionRig 0.64.0: worst packet-target source

Fleet overshoot telemetry now identifies the source responsible for the current
maximum packet-target deviation. The aggregate includes the source id plus the
target and observed utilization that produced the maximum delta.

This lets dashboards link a fleet-wide overshoot indicator directly to the
sensor row that needs inspection instead of scanning the catalog client-side.
When several measured sources have the same maximum delta, the deterministic
sorted source order keeps the first source as the representative.


## VisionRig 0.65.0: sustained pressure age

Fleet packet-target telemetry now exposes the longest currently sustained
pressure episode across the sensor fleet. The summary reports how many sources
are currently sustained, which source has the longest active run, when that run
started, and its current server-clock duration.

This gives dashboards a direct distinction between newly sustained pressure and
a source that has remained over target for a long period, without introducing a
new alert or capture policy.


## VisionRig 0.66.0: recurrence hotspot

Fleet packet-target telemetry now identifies the source with the highest
sustained recurrence count. The summary exposes the maximum recurrence count,
the source id behind it and that source's latest recovery-to-recurrence
interval.

This makes repeated transport instability directly actionable in dashboards
without scanning every catalog row. Equal recurrence counts retain the first
source in deterministic sorted source-id order.


## VisionRig 0.67.0: latest packet-target recovery

Fleet telemetry now exposes the most recent recovery from a sustained
packet-target pressure episode. The summary includes the recovered source id
and server-clock recovery timestamp.

This gives dashboards a direct recovery signal alongside current pressure and
recurrence hotspot telemetry, without changing attention or capture policy.


## VisionRig 0.68.0: latest recovery age

The fleet latest-recovery snapshot now includes `age_seconds`, derived from
the same server clock used by sensor ingress. Dashboards can therefore render
"recovered N seconds ago" without comparing timestamps client-side or depending
on client clock skew.

The age is process-local observability only and does not affect attention,
capture or adaptation policy.


## VisionRig 0.69.0: conservative packet-target stability

Packet-target stability no longer labels a source as stable merely because it
has runtime state and zero recurrence history. A source must now have both a
negotiated packet target and an observed packet utilization before it can be
classified as stable, recurring or flapping.

Sources with incomplete packet-target telemetry are reported as `unknown`.
This avoids presenting missing measurement data as evidence of stability.


## VisionRig 0.70.0: packet-target measurement coverage

Fleet telemetry now separates packet-target observability coverage into three
states: `complete`, `target_only`, and `unavailable`.

This makes an `unknown` compliance/stability result explainable at fleet level:
dashboards can distinguish producers that advertise a target but have not
reported observed utilization from older/offline sources with no packet-target
measurement at all.

The coverage counts are diagnostic only and do not affect attention, capture,
or adaptation policy.


## VisionRig 0.71.0: per-source packet-target measurement state

Sensor catalog now exposes a normalized `measurement` state inside each
source's `packet_target` block: `complete`, `target_only`, or
`unavailable`.

The same shared classifier now drives both catalog projection and fleet
measurement coverage, so dashboards can explain an `unknown` packet-target
status directly on the affected sensor row without duplicating inference logic
client-side.


## VisionRig 0.72.0: packet-target measurement gap diagnostics

Fleet observability now exposes a bounded, non-alerting list of runtime sources
with incomplete packet-target telemetry.

Each gap entry identifies the source, its measurement state, current presence,
negotiated target utilization and observed utilization. Complete sources are
excluded. Known catalog sources without runtime state are also excluded so this
list remains focused on producers that are currently connected but need a
telemetry upgrade.

Measurement gaps are deliberately separate from fleet attention: incomplete
observability should be visible without being promoted to an operational
incident.


## VisionRig 0.73.0: actionable measurement-gap remediation

Runtime sensor status now retains the producer's latest heartbeat schema id.
Packet-target measurement-gap diagnostics expose that schema together with
`required_schema_id: visionrig/sensor-heartbeat/v6`.

This turns an incomplete telemetry finding into an actionable producer upgrade
signal: dashboards can distinguish, for example, a v5 producer that only reports
the negotiated target from a v6 producer with complete measurement telemetry.

The measurement-gap list remains bounded to 32 deterministic source-id ordered
entries. Its total and truncation fields continue to describe the full fleet,
and it remains separate from operational attention.


## VisionRig 0.74.0: heartbeat schema coverage

Fleet observability now aggregates the heartbeat contract versions currently
used by connected runtime sources. The summary exposes counts for v2 through
v6, frame-only/no-heartbeat runtime sources, total runtime sources, and how many
producers require an upgrade to heartbeat v6 for complete packet-target
measurement telemetry.

This is upgrade/remediation telemetry only. It does not change attention,
capture authority, or producer negotiation policy.


## VisionRig 0.75.0: heartbeat upgrade candidates

Fleet observability now exposes a bounded list of runtime producers that still
require a heartbeat contract upgrade to v6.

Each candidate includes its source id, current presence, current heartbeat
schema, required v6 schema, and current packet-target measurement state. The
list is capped at 32 entries in deterministic source-id order, while total and
truncation fields describe the full runtime fleet.

This is remediation telemetry only and remains separate from operational
attention and control policy.


## VisionRig 0.76.0: heartbeat upgrade distance

Heartbeat upgrade candidates now include an objective migration distance:
`versions_behind` reports how many heartbeat contract versions a producer is
behind v6, while `upgrade_stage` distinguishes normal contract upgrades from
runtime sources that have not published a heartbeat yet.

The candidate list is prioritized before bounding: known contracts are ordered
by largest version gap first, then by source id. Frame-only/no-heartbeat sources
follow the known-version candidates and use `versions_behind = null`.

This keeps migration ordering deterministic and explainable without introducing
an opaque score, alert policy, or automatic producer upgrade behavior.


## VisionRig 0.77.0: producer readiness summary

Fleet telemetry now exposes a compact `producer_readiness` summary for UI and
operator dashboards.

The summary reports runtime producer count, heartbeat-v6 coverage, producers
still requiring a heartbeat upgrade, complete packet-target measurement count,
measurement-gap count, and normalized readiness ratios for heartbeat and packet
measurement coverage.

Ratios are `null` when no runtime producers exist. This is descriptive
progress telemetry only; it does not classify the fleet as good/bad and does
not alter attention, capture, or control policy.


## VisionRig 0.78.0: producer readiness transitions

Fleet telemetry now retains the latest process-local producer-readiness
transition instead of comparing against the immediately previous HTTP poll.

The transition records the previous readiness snapshot, server-clock change
timestamp, and deltas for heartbeat-v6 coverage and complete packet-target
measurement coverage. Repeated fleet/health reads do not advance or erase the
transition when readiness is unchanged.

An empty runtime fleet establishes no transition baseline, so startup with zero
producers does not create a misleading readiness regression or improvement.
This remains observational telemetry only.


## VisionRig 0.79.0: event-timed producer readiness transitions

Producer-readiness transition timestamps now belong to the runtime mutation
that changed readiness rather than the first fleet/health poll that observed it.

Heartbeat, encoded-frame ingress, SensorPacket ingress and runtime-source forget
refresh the process-local readiness transition state immediately after the
runtime mutation succeeds. Fleet, health and bootstrap reads are now
observational and do not advance transition timestamps.

This preserves the existing process-local/non-authoritative semantics while
making `changed_utc` suitable for UI timelines and migration diagnostics.


## VisionRig 0.80.0: per-source producer readiness

Sensor catalog now exposes a `producer_readiness` block for every known
source. Runtime sources report whether they are on heartbeat v6, whether a
contract upgrade is required, the current/required heartbeat schema, version
distance, upgrade stage, and whether packet-target measurement is complete.

Catalog-only sources deliberately use `null` for runtime readiness facts
instead of being treated as failed producers. This keeps row-level UI semantics
aligned with fleet producer readiness, which is based only on active runtime
sources.


## VisionRig 0.81.0: physical Kinect acceptance

`visionrig-kinect-acceptance` now produces a fail-closed, exact-checkout
physical evidence receipt for the cross-repository
`visionrig_physical_perception` release gate. A PASS requires real Kinect v2
RGB, raw depth, RGB-aligned depth and infrared across a bounded frame window,
meaningful semantic perception, and an exact-bound ModelRig admission receipt
that proves at least one WorldState change. The receipt contains no raw frame
bytes and grants no identity, memory, execution, scheduling or production
authority. See `docs/KINECT_V2.md`.


## VisionRig 0.81.1: shared producer-readiness semantics

Fleet heartbeat-upgrade candidates and per-source catalog
`producer_readiness` now use one shared source-level readiness classifier.

This is an internal semantic-hardening release: public payload shapes are
unchanged, but heartbeat upgrade stage, version distance, required schema and
packet-measurement state can no longer drift between fleet and catalog code
paths.
