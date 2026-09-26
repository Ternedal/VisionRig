# VisionRig observability and sensor control

VisionRig separates four concepts that a control surface must not blur:

1. **runtime sensor state** — what VisionRig has actually seen;
2. **producer declaration** — capabilities/device reported by the producer;
3. **operator metadata** — names, placement, role and desired enable state;
4. **producer desired state** — the minimal command surface a producer may read.

None of these is perception identity authority.

## Sensor runtime status

`GET /api/v1/sensors/status`

Schema: `visionrig/sensor-runtime-status/v4`.

Per source it exposes source/device identity, declared capabilities, last
sequence, accepted frames, producer-reported drops, heartbeat count,
`last_seen_utc`, `age_seconds`, `capture_active` and derived `online/stale/offline` presence.

## Heartbeat

`POST /api/v1/sensors/heartbeat` accepts
`visionrig/sensor-heartbeat/v2`. Cross-device clients use the same route
through the authenticated sensor gateway.

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
`visionrig/sensor-catalog/v7` and adds a bounded control summary:

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
`visionrig/sensor-fleet-summary/v2` with bounded operational aggregation:

- `state_revision`: current persisted semantic registry revision;
- total known/runtime sensor count;
- lifecycle counts for `active` and `retired`;
- presence counts for `online`, `stale`, `offline` and `unknown`;
- control counts for `converged`, `pending` and `unknown`;
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
state contains the bounded retained entries, next cursor and stream id. A normal
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

## UI bootstrap snapshot

`GET /api/v1/sensors/bootstrap` returns
`visionrig/sensor-bootstrap-snapshot/v3` with:

- `catalog`: the current sensor catalog;
- `fleet`: the current fleet summary;
- `sensor_state_revision`: current persisted semantic registry revision;
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
refreshes. Fleet v2 and bootstrap v3 expose the current revision, while each
change event carries the revision it reflects.

A client should track the highest event `state_revision` it has applied. If a
later fleet/bootstrap reports a greater revision and the change feed has not
delivered that revision, the client has detected registry/journal drift and
should fetch a new bootstrap snapshot. This detects inconsistency; it does not
pretend the two files are transactionally atomic.

Recommended UI flow:

1. fetch `/api/v1/sensors/bootstrap`;
2. render catalog/fleet;
3. poll
   `/api/v1/sensors/changes?after_cursor=<change_cursor>&stream_id=<change_stream_id>&wait_seconds=20`;
4. track the highest applied event `state_revision`;
5. if `gap=true`, `stream_reset=true`, or fleet `state_revision` is newer
   than the highest delivered semantic revision, fetch a new bootstrap snapshot;
6. separately refresh fleet/status at a low cadence for time-derived liveness.

## Health integration

`GET /health` uses `visionrig/health/v23` and advertises the desired-state
schema, persistent semantic state revision and discovery counts under the
sensor registry section.
The same sensor fleet summary is embedded as `sensor_fleet` for dashboards
that already poll health. Health also advertises the process-local sensor change
batch schema, durability, stream id and maximum wait under `sensor_changes`, and advertises the bootstrap snapshot
schema under `sensor_bootstrap`.

Only operational/control metadata is exposed. Raw images, depth arrays and
embeddings are never returned by these surfaces.
