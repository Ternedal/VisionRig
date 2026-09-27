# VisionRig observability and sensor control

VisionRig separates four concepts that a control surface must not blur:

1. **runtime sensor state** — what VisionRig has actually seen;
2. **producer declaration** — capabilities/device reported by the producer;
3. **operator metadata** — names, placement, role and desired enable state;
4. **producer desired state** — the minimal command surface a producer may read.

None of these is perception identity authority.

## Sensor runtime status

`GET /api/v1/sensors/status`

Schema: `visionrig/sensor-runtime-status/v16`.

Per source it exposes source/device identity, declared capabilities, last
sequence, accepted frames, producer-reported drops, heartbeat count,
optional latest accepted SensorPacket transport telemetry, negotiated producer
payload ceiling, negotiated packet compression and capability-refresh freshness,
`last_seen_utc`, `age_seconds`, `capture_active` and derived `online/stale/offline` presence.

## Heartbeat

`POST /api/v1/sensors/heartbeat` accepts
`visionrig/sensor-heartbeat/v2` through `visionrig/sensor-heartbeat/v6`. Cross-device clients use the same route
through the authenticated sensor gateway.

Heartbeat v3 adds an optional all-or-nothing producer transport negotiation
tuple:

- `negotiated_max_payload_bytes`;
- timezone-aware `capability_refreshed_utc`;
- `capability_refresh_seconds`.

Heartbeat v4 keeps those fields and adds
`negotiated_packet_compression` with one of `none`, `zlib` or `auto`.
V2 and v3 remain accepted unchanged for backward compatibility.

Runtime status and the catalog expose those values plus derived
`capability_refresh_age_seconds` and `capability_refresh_status`:
`current` while the last successful refresh is no older than twice the
producer's declared refresh interval, `stale` after that, and `unknown` when
the producer does not publish negotiation telemetry.

This is transient producer telemetry. It does not advance registry
`state_revision`, change desired state, or grant authority. The semantic
change feed emits `runtime_changed` when the negotiated byte ceiling or
compression strategy changes, but not when only the refresh timestamp advances.

## Operator sensor catalog

`PATCH /api/v1/sensors/{source_id}/metadata` stores operator-owned metadata:
`display_name`, `location`, `role` and `enabled`.

`GET /api/v1/sensors/catalog` joins registry metadata with current runtime
state. The registry is restart-safe by default at
`~/.visionrig/sensor-registry.json` and uses fsync plus atomic replacement.

The first valid heartbeat or encoded frame from an unknown `source_id` creates
an empty persistent registry entry automatically. Auto-registration is
idempotent and never overwrites operator-owned display name, location, role or
`enabled` state. This keeps previously seen sensors visible as known/offline
after service restart.

Persistent discovery stores the producer-described `source_type`, `device` and
normalized capabilities separately from operator metadata. Once a `source_id`
has been observed with a source type, reusing that id as another type is a 409
identity conflict. Device/capability observations may refresh without gaining
operator authority. Discovery also persists `first_seen_utc`, `last_seen_utc` and
`observation_count`, so a restarted service can show when an offline sensor was
first and most recently observed.

## Sensor lifecycle

`POST /api/v1/sensors/{source_id}/retire` is an explicit operator action.
Retirement persists `retired_utc`, preserves metadata/discovery history, and
sets desired `enabled=false`. If the sensor was enabled, that transition uses
the same revision/timestamp mechanism as any other control change.

`POST /api/v1/sensors/{source_id}/restore` clears `retired_utc` but keeps the
sensor disabled. This avoids a restore operation unexpectedly opening a camera;
an operator must explicitly enable it afterwards. Enabling a retired sensor
directly is rejected.

The catalog exposes `lifecycle.status` as `active` or `retired` plus the
retirement timestamp. Retirement is reversible and does not delete discovery
history.

`DELETE /api/v1/sensors/{source_id}` is the explicit permanent-forget action.
It is rejected unless the sensor is retired and either absent from runtime or
currently `offline`. Successful forget removes operator metadata, persistent
discovery/history, control revision/timestamp state and process-local ingress
sequence/runtime state. If that `source_id` appears again later, it is treated
as a new registration. An active/stale/online producer cannot be forgotten.

## Optimistic operator writes

The operator mutation endpoints accept an optional
`expected_state_revision=<n>` query parameter:

- `PATCH /api/v1/sensors/{source_id}/metadata`;
- `POST /api/v1/sensors/{source_id}/retire`;
- `POST /api/v1/sensors/{source_id}/restore`;
- `DELETE /api/v1/sensors/{source_id}`.

The revision comparison is performed inside the registry lock together with the
mutation. A mismatch returns HTTP 409 with:

```json
{
  "detail": {
    "code": "sensor_state_revision_conflict",
    "expected": 12,
    "current": 13
  }
}
```

The rejected mutation does not advance registry state or emit a semantic change
event. Successful mutation responses expose the resulting `state_revision`;
metadata uses `visionrig/sensor-metadata/v2`, lifecycle responses use
`visionrig/sensor-lifecycle/v2`, and permanent forget uses
`visionrig/sensor-forget/v2`.

The precondition is optional for compatibility, but a UI that edits state
should send the revision from its latest bootstrap/fleet state. On 409 it should
refresh bootstrap before retrying instead of replaying the stale write.

## Desired-state control contract

`GET /api/v1/sensors/{source_id}/desired-state` returns
`visionrig/sensor-desired-state/v2`:

- `source_id`
- `enabled`
- `revision`
- `production_authority=false`

The authenticated cross-device gateway exposes the same exact GET route and
forwards it only to the loopback core service. It does not expose the sensor
catalog, world state, journal, profiles, models or other control-plane data.

Unknown/unconfigured sensor ids default to `enabled=true`, matching registry
metadata defaults. Operators can preconfigure `enabled=false` before a device
ever connects.

The reference producer validates schema + source id through
`fetch_desired_state()`, closes capture while disabled, and reports
`capture_active` only after the source has actually opened or closed.

## Control convergence

`GET /api/v1/sensors/catalog` uses
`visionrig/sensor-catalog/v14` and adds a bounded control summary:

- `desired_enabled`: operator intent from the registry;
- `desired_revision`: revision of the current enabled command;
- `desired_changed_utc`: persisted UTC issue time for the current changed command;
- `effective_capture_active`: producer acknowledgement when available;
- `applied_revision`: exact command revision applied by the producer;
- `pending_seconds`: elapsed seconds since the current command was issued while not converged;
- `status`: `converged`, `pending` or `unknown`.

A sensor is `converged` only when both effective capture state and acknowledged
revision match the current desired state. A stale acknowledgement therefore
remains `pending` even if its boolean value happens to match. Missing effective
state or revision acknowledgement remains `unknown`. For non-converged
commands with a persisted issue timestamp, `pending_seconds` is computed from
that timestamp. VisionRig intentionally does not assign a timeout or "stuck"
label; control surfaces can choose their own alert threshold. The producer still
receives only its own minimal desired-state response and never gains catalog or
world-state access.

## Fleet summary

`GET /api/v1/sensors/fleet` returns
`visionrig/sensor-fleet-summary/v23` with bounded operational aggregation:

- `state_revision`: current persisted semantic registry revision;
- `change_consistency`: registry/journal revision comparison;
- total known/runtime sensor count;
- lifecycle counts for `active` and `retired`;
- presence counts for `online`, `stale`, `offline` and `unknown`;
- control counts for `converged`, `pending` and `unknown`;
- negotiated compression counts for `none`, `zlib`, `auto` and `unknown`;
- up to 32 attention entries plus `attention_total` and
  `attention_truncated`.

An attention entry is emitted for a pending control command or for an active
sensor whose presence is not online. This is descriptive telemetry, not a
health score or automatic policy. Retired sensors are not flagged merely for
being offline.

## Semantic sensor change feed

`GET /api/v1/sensors/changes?after_cursor=<n>&limit=<n>` returns
`visionrig/sensor-change-batch/v2`. The feed is bounded and process-local and
uses the same cursor/gap recovery semantics as the perception journal plus a
`stream_id` that identifies the current retained journal. In the normal
service process this bounded journal is persistent; explicitly ephemeral
configurations still use a process-local stream.

Every change event also carries `state_revision`, the persisted semantic
registry revision reflected by that event. Multiple events produced by one
registry mutation may share the same revision. Runtime-only producer
acknowledgement events carry the current registry revision without advancing it.

Events are emitted only for semantic state changes:

- first registration;
- changed device/capabilities discovery;
- operator metadata changes;
- desired control revision changes;
- retire, restore and permanent forget;
- producer `capture_active` / `applied_revision` changes.

Heartbeat refreshes that only update last-seen/counters do not emit events.
Likewise, online/stale/offline is derived from elapsed time; the passage of time
alone does not append an event. UI clients should use the change feed for
incremental semantic updates and continue polling fleet/status at a low cadence
for liveness transitions. A reported `gap=true` means the client fell behind
the bounded journal and should refresh catalog/fleet before continuing from the
returned cursor.

The service process persists the journal by default to
`~/.visionrig/sensor-changes.json` with atomic replacement. The persisted
state contains the bounded retained entries, next cursor, stream id and
`state_revision_high_water`. A normal
restart therefore restores the same stream and cursors. Configure
`VISIONRIG_SENSOR_CHANGE_JOURNAL_FILE` to another path, or set it to an empty
value to use process-local mode.

Clients should still send the current stream id back as
`/api/v1/sensors/changes?after_cursor=<n>&stream_id=<id>`. A new/ephemeral
journal, deleted journal file, or otherwise different stream returns
`stream_reset=true` rather than pretending an old cursor belongs to it.
Corrupt persistent journal files fail service startup explicitly instead of
silently discarding retained events.

The change endpoint also accepts `wait_seconds` from 0 through 30. When the
requested cursor is current and there is no gap/reset, VisionRig may hold the
request until a semantic event arrives or the timeout expires. Appending an
event wakes waiting clients immediately. Gap and stream-reset responses return
without waiting. A timeout returns a normal empty batch with the current cursor,
so clients can issue the next bounded long poll without special error handling.

## Registry / change-journal consistency

The semantic journal tracks a monotonic `state_revision_high_water`, the
highest registry revision represented by any successfully persisted journal
event. It is stored independently of the bounded retained event window, so
event eviction does not erase the watermark.

VisionRig compares that watermark with the current registry
`state_revision` and exposes
`visionrig/sensor-change-consistency/v1`:

- `synced`: registry and journal high-water are equal;
- `registry_ahead`: registry state is newer than the durable semantic journal;
- `journal_ahead`: journal watermark is newer than the registry file.

The status is included in fleet v3, bootstrap v4 and health. Health also exposes
the raw journal `state_revision_high_water`. No automatic repair is performed:
`registry_ahead` means clients should bootstrap from full state rather than
trust incremental history, while `journal_ahead` indicates persistent files
that should be investigated before treating incremental history as authoritative.

## UI bootstrap snapshot

`GET /api/v1/sensors/bootstrap` returns
`visionrig/sensor-bootstrap-snapshot/v25` with:

- `catalog`: the current sensor catalog;
- `fleet`: the current fleet summary;
- `sensor_state_revision`: current persisted semantic registry revision;
- `change_consistency`: server-side registry/journal consistency status;
- `change_cursor`: the semantic change-feed cursor to continue from;
- `change_stream_id`: identity of the current retained change journal.

The cursor is sampled **before** catalog/fleet construction. If a semantic
change races with snapshot construction, the client may see that state already
reflected in the snapshot and then receive the same change once from
`/changes`; the change cannot be skipped by sampling a cursor after it happened.
This intentionally favors harmless replay over missed control/lifecycle state.

The registry and semantic change journal are separate atomic files rather than
one cross-file transaction. To make that crash window observable, the registry
persists a monotonic semantic `state_revision`. It advances on registration,
device/capability changes, operator metadata/control changes, lifecycle changes
and permanent forget, but not on last-seen or observation-count-only heartbeat
refreshes. Fleet v3 and bootstrap v4 expose the current revision and consistency status,
while each change event carries the revision it reflects.

A client should track the highest event `state_revision` it has applied. If a
later fleet/bootstrap reports a greater revision and the change feed has not
delivered that revision, the client has detected registry/journal drift and
should fetch a new bootstrap snapshot. This detects inconsistency; it does not
pretend the two files are transactionally atomic.

Recommended UI flow:

1. fetch `/api/v1/sensors/bootstrap`;
2. render catalog/fleet and retain its `state_revision` for operator writes;
3. poll
   `/api/v1/sensors/changes?after_cursor=<change_cursor>&stream_id=<change_stream_id>&wait_seconds=20`;
4. track the highest applied event `state_revision`;
5. if `gap=true`, `stream_reset=true`, or fleet `state_revision` is newer
   than the highest delivered semantic revision, fetch a new bootstrap snapshot;
6. separately refresh fleet/status at a low cadence for time-derived liveness.

## Health integration

`GET /health` uses `visionrig/health/v53` and advertises the desired-state
schema, persistent semantic state revision and discovery counts under the
sensor registry section.
The same sensor fleet summary is embedded as `sensor_fleet` for dashboards
that already poll health. Health also advertises the process-local sensor change
batch schema, durability, stream id, revision high-water, consistency status and maximum wait under `sensor_changes`, and advertises the bootstrap snapshot
schema under `sensor_bootstrap`.

Only operational/control metadata is exposed. Raw images, depth arrays and
embeddings are never returned by these surfaces.


### SensorPacket transport telemetry

When a source's latest accepted visual input was SensorPacket v1/v2, runtime
status includes `packet_transport`
(`visionrig/sensor-packet-transport/v2`) with byte counts, per-plane
compression, numeric bytes saved, combined numeric compression ratio, payload
headroom, utilization of the configured ingress payload limit, and derived
normal/warning/critical payload status. It is deliberately transient observability,
not persistent registry state, and is cleared by a later accepted plain encoded
frame. Health advertises the transport telemetry schema under
`sensor_ingress.sensor_packet_transport_schema`.


Fleet summary v4 also counts transport status across known sensors
(`normal/warning/critical/unknown`). A warning or critical latest packet adds
that source to the existing bounded attention list with
`reason = packet_transport`, alongside any presence or control reasons. Health
advertises the exact thresholds under
`sensor_ingress.sensor_packet_payload_thresholds`: warning at 0.80 and
critical at 0.95.


### Capability refresh fleet attention

Fleet summary v5 aggregates producer capability-refresh freshness as
`current/stale/unknown`. A source whose heartbeat-v3 negotiation refresh is
`stale` enters the bounded attention list with reason
`capability_refresh`, alongside any existing presence, control or packet
transport reasons. Attention items include the latest negotiated payload ceiling
and refresh age for diagnosis.

`unknown` is intentionally not an alert: heartbeat-v2 producers do not publish
the negotiation tuple and remain compatible without being marked unhealthy.
This is observability only and never changes desired state or capture authority.


### Negotiated packet target utilization

Heartbeat v5 adds
`negotiated_packet_target_utilization` to the transient producer negotiation
surface. It reports the exact fraction of the negotiated payload ceiling that
the producer is currently targeting when adapting packet size. The value is
strictly between 0 and 1 and requires the complete negotiation tuple.

Runtime status v10 exposes the field per source. Heartbeat v2/v3/v4 remain
accepted for backward compatibility and report no target-utilization value.

Changing the target policy is operationally meaningful and emits one
`runtime_changed` event. Refreshing the same policy with a newer capability
timestamp remains quiet, preserving the no-heartbeat-spam change-feed contract.


### Observed packet utilization

Heartbeat v6 adds `observed_packet_utilization`, the actual size of the most
recent budgeted SensorPacket divided by the negotiated maximum payload size.
The value is bounded to 0..1 and is only valid together with the complete
producer negotiation tuple.

Runtime status v11 exposes both the negotiated target and the observed value,
so operators can distinguish policy from achieved transport behavior. The
reference Kinect producer updates the observed value after each successfully
encoded budgeted packet and publishes it on the next heartbeat.

Observed utilization is transient measurement telemetry. Changes do not emit
`runtime_changed` events, avoiding change-feed churn from normal frame-to-frame
size variation.


### Fleet packet-target compliance

Fleet summary v7 derives a bounded packet-target status from heartbeat v6
telemetry:

- `within_target` when the latest observed packet utilization is less than or
  equal to the negotiated target;
- `above_target` when the latest observed utilization exceeds the target;
- `unknown` when either value is unavailable.

An `above_target` source is added to bounded fleet attention with
`reason = packet_target`. Attention entries expose both target and observed
utilization plus the derived status. This remains transient transport
observability; it does not advance registry state, modify desired state, or
emit semantic change events.


### Sustained packet-target attention

VisionRig 0.56.0 keeps the instantaneous fleet compliance classification, but
does not raise `packet_target` attention for a single oversized observation.
Runtime status v12 tracks `packet_target_above_streak`, the number of
consecutive heartbeat-v6 measurements whose observed utilization exceeds the
negotiated target.

Fleet summary v8 advertises
`packet_target_attention_streak_threshold = 3`. The source enters bounded
attention only when the current status is `above_target` and the streak has
reached that threshold. A within-target measurement or missing comparison data
resets the streak to zero immediately.

This debounce state is process-local transport telemetry. It does not advance
registry state, alter desired capture state, or emit semantic change events.


### Packet-target pressure duration

VisionRig 0.57.0 adds temporal context to the packet-target debounce signal.
Runtime status v13 exposes:

- `packet_target_above_since_utc` while a consecutive over-target run is active;
- `packet_target_above_seconds`, derived from the server clock;
- `packet_target_last_above_utc`, retained after recovery for diagnosis.

Fleet summary v9 aggregates the current pressure state as `clear`,
`transient`, `sustained` or `unknown`. `transient` means the latest
measurement is above target but the configured three-heartbeat attention
threshold has not yet been reached. `sustained` means the threshold has been
reached and the source is eligible for `packet_target` attention.

The timestamps and duration are process-local transport telemetry. They do not
advance registry state, alter desired capture state, or generate semantic
change-feed events.


### Sustained pressure episodes and recovery

VisionRig 0.58.0 extends the process-local packet-target diagnostics with
episode semantics. Runtime status v14 exposes:

- `packet_target_sustained_episode_count`, incremented exactly once when an
  over-target streak reaches the shared sustained threshold;
- `packet_target_last_recovered_utc`, updated when a sustained episode ends.

Long-running sustained pressure therefore remains one episode instead of being
counted on every heartbeat. A transient run that recovers before reaching the
threshold does not increment the episode count and does not update the sustained
recovery timestamp.

Fleet summary v10 exposes
`packet_target_sustained_episode_total` across runtime sources and
`packet_target_recovered_sources`, the number of sources that have recovered
from at least one sustained episode. Attention entries include the per-source
episode count and last recovery timestamp.

These fields remain transient operational telemetry and do not advance semantic
registry revisions or change capture authority.


### Sustained pressure recurrence

VisionRig 0.59.0 adds recurrence telemetry on top of sustained episode and
recovery tracking. Runtime status v15 exposes:

- `packet_target_recurrence_count`, incremented when a sustained episode begins
  after at least one prior sustained recovery;
- `packet_target_last_recurrence_seconds`, the server-clock interval from the
  most recent sustained recovery to the point where the new episode reaches the
  shared sustained threshold.

The first sustained episode is not a recurrence. Remaining continuously above
target does not increase recurrence count, and transient over-target runs that
recover before the sustained threshold are not recurrences.

Fleet summary v11 exposes `packet_target_recurrence_total` and
`packet_target_recurring_sources`. Attention entries carry the per-source
recurrence count and latest recurrence interval so an operator can identify
repeatedly unstable producers without introducing automatic capture policy.

Like the other packet-target diagnostics, recurrence data is process-local
operational telemetry and does not advance semantic registry state or emit
heartbeat-driven semantic change events.


### Packet-target stability classification

VisionRig 0.60.0 derives a fleet-level stability classification from recurrence
telemetry. The service accepts
`packet_target_flap_window_seconds` when constructed; it defaults to 120
seconds and must be greater than zero.

Fleet summary v12 exposes `packet_target_stability` counts for:

- `stable`: runtime source exists and has no sustained recurrence;
- `flapping`: the latest recurrence interval is less than or equal to the
  configured flap window;
- `recurring`: at least one sustained recurrence exists and the latest
  recurrence interval is longer than the flap window;
- `unknown`: no runtime source exists for the known sensor.

The fleet response also publishes the effective
`packet_target_flap_window_seconds` so dashboards interpret the classification
against the same threshold used by the service. Current attention entries carry
the per-source stability status, but stability itself does not create an
attention reason or alter capture policy.

Health embeds the same fleet v12 payload. Bootstrap advances with the fleet
contract so UI clients receive one internally consistent snapshot.


### Catalog packet-target stability projection

VisionRig 0.61.0 projects the recurrence stability model into sensor catalog v9.
Each source now includes a `packet_target` object with:

- `stability`: `stable`, `recurring`, `flapping` or `unknown`;
- `flap_window_seconds`: the effective configured classification window;
- `recurrence_count`: sustained recurrences observed in process-local runtime;
- `last_recurrence_seconds`: latest recovery-to-recurrence interval.

The catalog and fleet use one shared classifier, preventing the same sensor from
being labelled differently across UI surfaces. A known sensor with no current
runtime is `unknown`, while an active runtime with no recurrence history is
`stable`.

Bootstrap v12 embeds catalog v9 alongside fleet v12. Health advances to v40.
The projection remains process-local transport observability and has no control
authority.


### Complete catalog packet-target diagnostics

VisionRig 0.62.0 expands sensor catalog v10 so the per-source
`packet_target` object is a complete bounded diagnostic projection rather than
only a stability summary. It exposes:

- `status`: `within_target`, `above_target` or `unknown`;
- `pressure`: `clear`, `transient`, `sustained` or `unknown`;
- target and observed utilization;
- the shared sustained-attention threshold and current above-target streak;
- current above-target start/duration plus latest above-target observation;
- sustained episode count and latest sustained recovery;
- stability, flap window, recurrence count and latest recurrence interval.

Catalog, fleet and attention now derive compliance, pressure and stability from
the same server-side classifier helpers. UI clients therefore do not need to
duplicate threshold logic or join fleet attention back onto catalog rows.

Known sensors without runtime expose `unknown` compliance/pressure/stability,
zero counters and null timestamps. Bootstrap v13 embeds catalog v10 alongside
fleet v12; health advances to v41. All fields remain process-local operational
telemetry with no capture authority.


### Packet-target overshoot telemetry

VisionRig 0.63.0 adds magnitude to the existing packet-target compliance
diagnostics. For a runtime source with both target and observed utilization,
VisionRig derives:

- `overshoot_delta = max(0, observed - target)`;
- `overshoot_ratio = overshoot_delta / target`.

Both values are rounded to six decimal places. A measured source that is at or
below target reports `0.0` for both fields, while a source without a complete
target/observed pair reports `null`.

Catalog v11 exposes these values in each source's `packet_target` block.
Bounded fleet attention entries expose the same values as
`packet_target_overshoot_delta` and `packet_target_overshoot_ratio`.

Fleet summary v13 additionally exposes `packet_target_overshoot` with
`measured_sources`, `max_delta` and `max_ratio`. If no source has a
complete measurement pair, the maxima are `null`; if all measured sources are
within target, both maxima are `0.0`.

Bootstrap v14 embeds catalog v11 and fleet v13; health advances to v42. The
overshoot fields are process-local operational telemetry and create no semantic
change events or automatic control action.


### Worst packet-target source

VisionRig 0.64.0 extends fleet summary v14 so
`packet_target_overshoot` identifies the source behind the current maximum
overshoot. In addition to measured-source count and maximum delta/ratio, the
aggregate exposes:

- `worst_source_id`;
- `worst_target_utilization`;
- `worst_observed_utilization`.

The representative is selected by maximum overshoot delta. Source ids are
processed in deterministic sorted order, so equal maximum deltas retain the
first source rather than producing an unstable winner between requests.

If no complete target/observed pair exists, all worst-source fields remain
`null`. A measured fleet entirely within target still identifies the first
measured source with a maximum delta of `0.0`; this reflects which measurement
anchors the aggregate, not an alert.

Bootstrap v15 embeds fleet v14 and health advances to v43. This remains
process-local diagnostic telemetry and does not alter attention or control
policy.


### Sustained packet-target pressure age

VisionRig 0.65.0 extends fleet summary v15 with
`packet_target_sustained_pressure`. The aggregate contains:

- `sources`: the number of sources whose current packet-target pressure state
  is `sustained`;
- `longest_seconds`: current server-clock duration of the longest sustained
  over-target run;
- `longest_source_id`: source owning that longest active run;
- `longest_since_utc`: start of that source's current consecutive over-target
  run.

Only sources that have crossed the shared sustained streak threshold participate
in this aggregate. Transient over-target sources are deliberately excluded even
though they may already have a non-zero `packet_target_above_seconds`.

If no source is currently sustained, the source count is zero and the remaining
fields are `null`. Equal durations retain the first source in deterministic
sorted source-id order.

Bootstrap v16 embeds fleet v15 and health advances to v44. The summary is
process-local operational telemetry only; it creates no semantic event and
changes no capture or adaptation policy.


### Packet-target recurrence hotspot

VisionRig 0.66.0 extends fleet summary v16 with
`packet_target_recurrence_hotspot`. The aggregate contains:

- `max_recurrence_count`: highest sustained recurrence count among runtime
  sources;
- `source_id`: source owning that highest count;
- `last_recurrence_seconds`: that source's latest recovery-to-recurrence
  interval.

Sources with zero recurrence do not become a hotspot. If no source has
recurred, the count is zero and the source/interval fields are `null`.
Equal positive counts retain the first source in deterministic sorted source-id
order.

Bootstrap v17 embeds fleet v16 and health advances to v45. The hotspot is
process-local diagnostic telemetry only; it does not create attention, semantic
events or automatic control action.


### Latest packet-target recovery

VisionRig 0.67.0 extends fleet summary v17 with
`packet_target_latest_recovery`. The aggregate exposes:

- `source_id`: source with the most recent sustained-pressure recovery;
- `recovered_utc`: server-clock timestamp of that recovery.

Only recoveries from sustained episodes participate. Transient over-target runs
that clear before the sustained threshold do not create recovery telemetry.
If no runtime source has recovered from sustained pressure, both fields are
`null`.

When two sources share an identical recovery timestamp, deterministic sorted
source-id processing retains the first source.

Bootstrap v18 embeds fleet v17 and health advances to v46. The latest-recovery
summary is process-local operational telemetry only; it creates no semantic
change event, alert, or control action.


### Latest recovery age

VisionRig 0.68.0 extends fleet summary v18 so
`packet_target_latest_recovery` also exposes `age_seconds`.

The age is computed from the same effective server clock used by
`SensorIngress`, against the selected source's
`packet_target_last_recovered_utc`. It is clamped at zero and rounded to three
decimal places. This avoids client clock skew and keeps recovery age consistent
with the existing server-clock packet-pressure durations.

If no sustained recovery exists, `source_id`, `recovered_utc` and
`age_seconds` are all `null`.

Bootstrap v19 embeds fleet v18 and health advances to v47. Recovery age remains
process-local operational telemetry and creates no semantic event or control
action.


### Conservative packet-target stability

VisionRig 0.69.0 tightens the packet-target stability classifier used by fleet
summary v19 and sensor catalog v12.

A runtime source is classified as `unknown` unless both
`negotiated_packet_target_utilization` and
`observed_packet_utilization` are present. Only sources with a complete
measurement pair can then be classified as `stable`, `recurring` or
`flapping` from their recurrence history.

This is an intentional semantic correction: absence of recurrence evidence is
not treated as evidence of stability when packet-target measurement itself is
missing. Heartbeat v5 target-only producers and older producer contracts
therefore remain `unknown`.

Bootstrap v20 embeds catalog v12 and fleet v19; health advances to v48. The
change affects classification only and introduces no new attention, semantic
events or control behavior.


### Packet-target measurement coverage

VisionRig 0.70.0 extends fleet summary v20 with
`packet_target_measurement_coverage`:

- `complete`: both negotiated target utilization and observed utilization are
  present;
- `target_only`: a negotiated target exists but observed utilization is
  missing;
- `unavailable`: no negotiated packet target is available, including legacy
  runtime sources and known sensors without runtime telemetry.

The three counters are mutually exclusive and sum to the fleet source total.
They explain why compliance, pressure, or stability may be `unknown` without
changing those classifiers themselves.

Bootstrap v21 embeds fleet v20 and health advances to v49. Measurement coverage
is process-local operational telemetry only and creates no semantic event or
control action.


### Per-source packet-target measurement state

VisionRig 0.71.0 projects the fleet measurement-coverage model into sensor
catalog v13. Each source's `packet_target` object now includes
`measurement` with one of:

- `complete`: target and observed utilization are both available;
- `target_only`: target is available but observed utilization is missing;
- `unavailable`: no usable packet-target measurement is available.

A single shared server-side classifier drives both catalog `measurement` and
fleet `packet_target_measurement_coverage`, preventing semantic drift between
row-level and fleet-level observability.

Bootstrap v22 embeds catalog v13; health advances to v50. This is diagnostic
projection only and introduces no new attention, semantic event, or control
behavior.


### Packet-target measurement gaps

VisionRig 0.72.0 extends fleet summary v21 with a bounded diagnostics list for
connected runtime sources whose packet-target `measurement` is not
`complete`.

The fleet payload exposes:

- `packet_target_measurement_gaps`: up to 32 runtime sources, in deterministic
  source-id order, with `source_id`, `measurement`, `presence`,
  `negotiated_packet_target_utilization`, and
  `observed_packet_utilization`;
- `packet_target_measurement_gap_total`: total number of connected runtime
  sources with incomplete packet-target measurement;
- `packet_target_measurement_gaps_truncated`: whether the bounded list omits
  additional gap sources.

Catalog-only sources without runtime state are intentionally excluded from this
list because there is no active producer to remediate. `complete` runtime
sources are also excluded.

These diagnostics do not contribute to `attention_total` and do not add an
attention reason. This keeps telemetry-upgrade work visible without treating it
as an operational incident.

Bootstrap v23 embeds fleet v21 and health advances to v51. The gap list remains
process-local operational telemetry and creates no semantic event or control
action.


### Measurement-gap remediation metadata

VisionRig 0.73.0 advances runtime status to v16 and fleet summary to v22.

Each runtime source now retains `heartbeat_schema_id`, representing the most
recent heartbeat contract accepted for that producer. Frame-only runtime
sources expose `null` until a heartbeat is observed.

Each `packet_target_measurement_gaps` item now additionally exposes:

- `heartbeat_schema_id`: producer contract currently observed;
- `required_schema_id`: `visionrig/sensor-heartbeat/v6`, the contract that
  supports observed packet utilization and therefore complete packet-target
  measurement.

The gap list remains capped at 32 entries in deterministic source-id order.
`packet_target_measurement_gap_total` remains unbounded, and
`packet_target_measurement_gaps_truncated` indicates omitted rows. The
diagnostic list still does not contribute to `attention_total`.

Sensor catalog advances to v14 because its nested runtime projection now
includes `heartbeat_schema_id`. Bootstrap v24 embeds fleet v22 and catalog
v14; health advances to v52. These fields remain process-local diagnostics and
do not create semantic events or control changes.


### Heartbeat schema coverage

VisionRig 0.74.0 extends fleet summary v23 with
`heartbeat_schema_coverage`.

The aggregate contains:

- `runtime_sources`: connected/process-local runtime sources;
- `v2` through `v6`: count by latest accepted heartbeat contract;
- `no_heartbeat`: runtime sources created from frame ingress that have not yet
  published a heartbeat;
- `upgrade_required`: runtime sources whose latest heartbeat is not v6,
  including frame-only sources without a heartbeat.

Catalog-only sensors without runtime state are intentionally excluded. The
aggregate therefore describes producer runtime coverage, while
`packet_target_measurement_coverage` continues to describe the whole known
fleet.

Heartbeat v6 remains the remediation target because it is the first contract
that carries observed packet utilization. A producer running v6 is not counted
as requiring a contract upgrade even if some separate runtime condition causes
measurement telemetry to be unavailable.

Bootstrap v25 embeds fleet v23 and health advances to v53. Heartbeat schema
coverage is process-local diagnostics only and creates no semantic event,
attention reason, or control action.


### Heartbeat upgrade candidates

VisionRig 0.75.0 extends fleet summary v24 with a bounded producer migration
list derived from `heartbeat_schema_coverage`.

The fleet payload exposes:

- `heartbeat_upgrade_candidates`: up to 32 runtime sources whose latest
  heartbeat contract is not v6, in deterministic source-id order;
- `heartbeat_upgrade_candidate_total`: total number of runtime sources that
  require a heartbeat contract upgrade;
- `heartbeat_upgrade_candidates_truncated`: whether additional candidates are
  omitted from the bounded list.

Each candidate contains `source_id`, `presence`, `current_schema_id`,
`required_schema_id`, and the current packet-target `measurement` state.
Frame-only sources with no heartbeat use `current_schema_id = null` and remain
valid upgrade candidates.

The candidate total matches
`heartbeat_schema_coverage.upgrade_required`. Candidates remain diagnostic
migration work only: they do not affect `attention_total`, desired state,
capture authority, or semantic change events.

Bootstrap v26 embeds fleet v24 and health advances to v54.


### Heartbeat upgrade distance

VisionRig 0.76.0 extends fleet summary v25 heartbeat upgrade candidates with
two remediation fields:

- `upgrade_stage`: `contract_upgrade` for known v2-v5 producers or
  `establish_heartbeat` for runtime sources with no accepted heartbeat;
- `versions_behind`: integer distance from heartbeat v6 for known contracts
  (v5=1, v4=2, v3=3, v2=4), or `null` when no heartbeat contract is known.

The candidate list is sorted across the full runtime candidate set before the
32-entry bound is applied. Known contracts are ordered by descending
`versions_behind`, then source id. Sources without a heartbeat follow the
known-version candidates, also in deterministic source-id order.

This ensures the bounded list cannot hide more outdated producers merely
because alphabetically earlier v5 producers filled the first 32 slots.

`heartbeat_upgrade_candidate_total` remains the unbounded total and
`heartbeat_upgrade_candidates_truncated` reports whether the prioritized list
was cut. The ordering is remediation metadata only and does not alter
`attention_total`, desired state, capture authority, or semantic events.

Bootstrap v27 embeds fleet v25 and health advances to v55.
