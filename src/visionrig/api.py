"""HTTP surface for the standalone VisionRig service."""
from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from . import __version__
from .capabilities import probe_capabilities
from .contracts import PerceptionEvent, SourceDescriptor, WorldSnapshot
from .http_io import read_bounded_body
from .journal import EventBatch
from .modelrig_bridge import ModelRigPerceptionPublisher
from .pipeline import Frame, PassthroughStage, PerceptionPipeline
from .runtime import VisionRuntime
from .sensor_events import SensorChangeBatch, SensorChangeJournal
from .sensor_ingress import (
    SensorDecodeError,
    SensorFrameReceipt,
    SensorHeartbeat,
    SensorHeartbeatReceipt,
    SensorIngress,
    SensorIngressBusy,
    SensorMediaTypeError,
    SensorPayloadTooLarge,
    SensorSequenceError,
)
from .sensor_registry import (
    SensorDesiredState,
    SensorIdentityConflict,
    SensorMetadataPatch,
    SensorRegistry,
    SensorStateRevisionConflict,
)


class IngestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: SourceDescriptor
    frame_sequence: int = Field(ge=0)
    payload: dict
    dropped_frames: int = Field(default=0, ge=0)


def create_app(
    pipeline: PerceptionPipeline | None = None,
    *,
    max_sensor_frame_bytes: int = 8 * 1024 * 1024,
    sensor_stale_after_seconds: float = 15.0,
    sensor_offline_after_seconds: float = 60.0,
    modelrig_publisher: ModelRigPerceptionPublisher | None = None,
    sensor_registry: SensorRegistry | None = None,
    sensor_change_journal: SensorChangeJournal | None = None,
) -> FastAPI:
    app = FastAPI(title="VisionRig", version=__version__)
    selected_pipeline = pipeline or PerceptionPipeline((PassthroughStage(),))
    runtime = VisionRuntime(
        selected_pipeline,
        event_sinks=((modelrig_publisher,) if modelrig_publisher is not None else ()),
    )
    sensor_ingress = SensorIngress(
        runtime,
        max_payload_bytes=max_sensor_frame_bytes,
        stale_after_seconds=sensor_stale_after_seconds,
        offline_after_seconds=sensor_offline_after_seconds,
    )
    registry = sensor_registry or SensorRegistry()
    sensor_changes = sensor_change_journal or SensorChangeJournal()

    def raise_state_revision_conflict(
        exc: SensorStateRevisionConflict,
    ) -> None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "sensor_state_revision_conflict",
                "expected": exc.expected,
                "current": exc.current,
            },
        ) from exc

    def append_sensor_change(
        *,
        kind,
        source_id: str,
        payload: dict[str, object] | None = None,
    ):
        return sensor_changes.append(
            kind=kind,
            source_id=source_id,
            payload=payload,
            state_revision=registry.state_revision,
        )

    def runtime_source_for(source_id: str):
        return next(
            (
                source
                for source in sensor_ingress.stats().sources
                if source.source_id == source_id
            ),
            None,
        )

    def emit_registration_changes(
        source_id: str,
        *,
        was_registered: bool,
        previous_discovery,
    ) -> None:
        current_discovery = registry.get_discovery(source_id)
        if not was_registered and registry.contains(source_id):
            append_sensor_change(
                kind="registered",
                source_id=source_id,
                payload={
                    "discovery": (
                        asdict(current_discovery)
                        if current_discovery is not None
                        else None
                    )
                },
            )
        elif (
            previous_discovery is not None
            and current_discovery is not None
            and (
                previous_discovery.source_type != current_discovery.source_type
                or previous_discovery.device != current_discovery.device
                or previous_discovery.capabilities != current_discovery.capabilities
            )
        ):
            append_sensor_change(
                kind="discovery_changed",
                source_id=source_id,
                payload={
                    "discovery": asdict(current_discovery),
                },
            )

    def emit_runtime_change(source_id: str, previous_runtime) -> None:
        current_runtime = runtime_source_for(source_id)
        if current_runtime is None:
            return
        if (
            previous_runtime is None
            or previous_runtime.capture_active != current_runtime.capture_active
            or previous_runtime.applied_revision != current_runtime.applied_revision
        ):
            append_sensor_change(
                kind="runtime_changed",
                source_id=source_id,
                payload={
                    "capture_active": current_runtime.capture_active,
                    "applied_revision": current_runtime.applied_revision,
                    "presence": current_runtime.presence,
                },
            )

    def sensor_fleet_summary_payload() -> dict[str, object]:
        runtime_status = sensor_ingress.stats()
        runtime_by_id = {
            source.source_id: source
            for source in runtime_status.sources
        }
        metadata_by_id = {
            entry.source_id: entry
            for entry in registry.list()
        }
        source_ids = sorted(set(runtime_by_id) | set(metadata_by_id))

        lifecycle_counts = {"active": 0, "retired": 0}
        presence_counts = {
            "online": 0,
            "stale": 0,
            "offline": 0,
            "unknown": 0,
        }
        control_counts = {
            "converged": 0,
            "pending": 0,
            "unknown": 0,
        }
        attention = []
        attention_total = 0

        for source_id in source_ids:
            runtime_source = runtime_by_id.get(source_id)
            metadata = registry.get(source_id)
            lifecycle = (
                "retired"
                if metadata.retired_utc is not None
                else "active"
            )
            lifecycle_counts[lifecycle] += 1

            presence = (
                runtime_source.presence
                if runtime_source is not None
                else "unknown"
            )
            presence_counts[presence] += 1

            effective = (
                runtime_source.capture_active
                if runtime_source is not None
                else None
            )
            applied_revision = (
                runtime_source.applied_revision
                if runtime_source is not None
                else None
            )
            control_state = registry.control_state(source_id)
            if effective is None or applied_revision is None:
                control_status = "unknown"
            elif (
                applied_revision == control_state.revision
                and effective == metadata.enabled
            ):
                control_status = "converged"
            else:
                control_status = "pending"
            control_counts[control_status] += 1

            needs_attention = (
                control_status == "pending"
                or (
                    lifecycle == "active"
                    and presence != "online"
                )
            )
            if needs_attention:
                attention_total += 1
            if needs_attention and len(attention) < 32:
                attention.append(
                    {
                        "source_id": source_id,
                        "lifecycle": lifecycle,
                        "presence": presence,
                        "control_status": control_status,
                        "pending_seconds": (
                            registry.control_pending_seconds(source_id)
                            if control_status == "pending"
                            else None
                        ),
                    }
                )

        return {
            "schema": "visionrig/sensor-fleet-summary/v2",
            "state_revision": registry.state_revision,
            "total": len(source_ids),
            "lifecycle": lifecycle_counts,
            "presence": presence_counts,
            "control": control_counts,
            "attention": attention,
            "attention_total": attention_total,
            "attention_truncated": attention_total > len(attention),
        }

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "visionrig",
            "schema": "visionrig/health/v24",
            "perception_schema": "visionrig/perception-event/v3",
            "stages": selected_pipeline.stages,
            "capture_queue": asdict(runtime.stats()),
            "event_sinks": asdict(runtime.sink_stats()),
            "modelrig_bridge": (
                {
                    "enabled": True,
                    "endpoint": modelrig_publisher.endpoint,
                    "stats": asdict(modelrig_publisher.stats()),
                    "last_status": (
                        modelrig_publisher.last_result.status
                        if modelrig_publisher.last_result is not None
                        else None
                    ),
                }
                if modelrig_publisher is not None
                else {"enabled": False}
            ),
            "sensor_ingress": {
                "schema": "visionrig/sensor-ingress/v2",
                "max_frame_bytes": sensor_ingress.max_payload_bytes,
                "media_types": ["image/jpeg", "image/png", "image/webp"],
                "overload_policy": "reject",
                "runtime": asdict(sensor_ingress.stats()),
            },
            "sensor_registry": {
                "schema": "visionrig/sensor-registry/v7",
                "state_revision": registry.state_revision,
                "entries": len(registry.list()),
                "discovered": len(registry.list_discovery()),
                "desired_state_schema": "visionrig/sensor-desired-state/v2",
            },
            "sensor_fleet": sensor_fleet_summary_payload(),
            "sensor_changes": {
                "schema": "visionrig/sensor-change-batch/v2",
                "durability": (
                    "persistent" if sensor_changes.persistent else "process-local"
                ),
                "stream_id": sensor_changes.stream_id,
                "max_wait_seconds": 30,
            },
            "sensor_bootstrap": {
                "schema": "visionrig/sensor-bootstrap-snapshot/v3",
            },
        }

    @app.get("/api/v1/capabilities")
    def capabilities() -> dict[str, object]:
        return probe_capabilities()

    @app.get("/api/v1/capture/stats")
    def capture_stats() -> dict[str, int]:
        return asdict(runtime.stats())

    @app.get("/api/v1/sensors/status")
    def sensor_status() -> dict[str, object]:
        return asdict(sensor_ingress.stats())

    @app.get("/api/v1/sensors/fleet")
    def sensor_fleet_summary() -> dict[str, object]:
        return sensor_fleet_summary_payload()

    @app.get(
        "/api/v1/sensors/changes",
        response_model=SensorChangeBatch,
    )
    def sensor_change_feed(
        after_cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=64, ge=1, le=256),
        stream_id: str | None = Query(default=None, min_length=1, max_length=128),
        wait_seconds: float = Query(default=0.0, ge=0.0, le=30.0),
    ) -> SensorChangeBatch:
        return sensor_changes.wait_for_changes(
            after_cursor=after_cursor,
            limit=limit,
            expected_stream_id=stream_id,
            wait_seconds=wait_seconds,
        )

    @app.get("/api/v1/sensors/catalog")
    def sensor_catalog() -> dict[str, object]:
        runtime_status = sensor_ingress.stats()
        runtime_by_id = {source.source_id: source for source in runtime_status.sources}
        metadata_by_id = {entry.source_id: entry for entry in registry.list()}
        source_ids = sorted(set(runtime_by_id) | set(metadata_by_id))
        sources = []
        for source_id in source_ids:
            runtime_source = runtime_by_id.get(source_id)
            metadata = registry.get(source_id)
            effective = (
                runtime_source.capture_active
                if runtime_source is not None
                else None
            )
            applied_revision = (
                runtime_source.applied_revision
                if runtime_source is not None
                else None
            )
            control_state = registry.control_state(source_id)
            desired_revision = control_state.revision
            if effective is None or applied_revision is None:
                control_status = "unknown"
            elif (
                applied_revision == desired_revision
                and effective == metadata.enabled
            ):
                control_status = "converged"
            else:
                control_status = "pending"
            discovery = registry.get_discovery(source_id)
            sources.append(
                {
                    "source_id": source_id,
                    "runtime": (
                        asdict(runtime_source)
                        if runtime_source is not None
                        else None
                    ),
                    "metadata": asdict(metadata),
                    "lifecycle": {
                        "status": (
                            "retired"
                            if metadata.retired_utc is not None
                            else "active"
                        ),
                        "retired_utc": metadata.retired_utc,
                    },
                    "discovery": (
                        asdict(discovery)
                        if discovery is not None
                        else None
                    ),
                    "control": {
                        "desired_enabled": metadata.enabled,
                        "desired_revision": desired_revision,
                        "desired_changed_utc": control_state.changed_utc,
                        "effective_capture_active": effective,
                        "applied_revision": applied_revision,
                        "pending_seconds": (
                            registry.control_pending_seconds(source_id)
                            if control_status != "converged"
                            else None
                        ),
                        "status": control_status,
                    },
                }
            )
        return {
            "schema": "visionrig/sensor-catalog/v7",
            "sources": sources,
        }

    @app.get("/api/v1/sensors/bootstrap")
    def sensor_bootstrap_snapshot() -> dict[str, object]:
        # Sample the change cursor before building state. Changes racing with
        # snapshot construction may be replayed, but cannot be missed.
        change_state = sensor_changes.read(after_cursor=0, limit=1)
        baseline_cursor = change_state.newest_available_cursor or 0
        return {
            "schema": "visionrig/sensor-bootstrap-snapshot/v3",
            "sensor_state_revision": registry.state_revision,
            "change_stream_id": sensor_changes.stream_id,
            "change_cursor": baseline_cursor,
            "catalog": sensor_catalog(),
            "fleet": sensor_fleet_summary_payload(),
        }

    @app.get(
        "/api/v1/sensors/{source_id}/desired-state",
        response_model=SensorDesiredState,
    )
    def sensor_desired_state(source_id: str) -> SensorDesiredState:
        if not source_id or len(source_id) > 128:
            raise HTTPException(
                status_code=422,
                detail="source_id must contain 1..128 characters",
            )
        return registry.desired_state(source_id)

    @app.post("/api/v1/sensors/{source_id}/retire")
    def retire_sensor(
        source_id: str,
        expected_state_revision: int | None = Query(default=None, ge=0),
    ) -> dict[str, object]:
        previous = registry.get(source_id)
        try:
            metadata = registry.retire(
                source_id,
                expected_state_revision=expected_state_revision,
            )
        except SensorStateRevisionConflict as exc:
            raise_state_revision_conflict(exc)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if previous.retired_utc is None and metadata.retired_utc is not None:
            desired = registry.desired_state(source_id)
            append_sensor_change(
                kind="retired",
                source_id=source_id,
                payload={
                    "retired_utc": metadata.retired_utc,
                    "desired_enabled": desired.enabled,
                    "desired_revision": desired.revision,
                },
            )
        return {
            "schema": "visionrig/sensor-lifecycle/v2",
            "state_revision": registry.state_revision,
            "status": "retired",
            "metadata": asdict(metadata),
            "desired_state": registry.desired_state(source_id).model_dump(),
        }

    @app.post("/api/v1/sensors/{source_id}/restore")
    def restore_sensor(
        source_id: str,
        expected_state_revision: int | None = Query(default=None, ge=0),
    ) -> dict[str, object]:
        previous = registry.get(source_id)
        try:
            metadata = registry.restore(
                source_id,
                expected_state_revision=expected_state_revision,
            )
        except SensorStateRevisionConflict as exc:
            raise_state_revision_conflict(exc)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if previous.retired_utc is not None and metadata.retired_utc is None:
            append_sensor_change(
                kind="restored",
                source_id=source_id,
                payload={"enabled": metadata.enabled},
            )
        return {
            "schema": "visionrig/sensor-lifecycle/v2",
            "state_revision": registry.state_revision,
            "status": "active",
            "metadata": asdict(metadata),
            "desired_state": registry.desired_state(source_id).model_dump(),
        }

    @app.delete("/api/v1/sensors/{source_id}")
    def forget_sensor(
        source_id: str,
        expected_state_revision: int | None = Query(default=None, ge=0),
    ) -> dict[str, object]:
        if not registry.contains(source_id):
            raise HTTPException(status_code=404, detail="sensor is not registered")

        metadata = registry.get(source_id)
        if metadata.retired_utc is None:
            raise HTTPException(
                status_code=409,
                detail="sensor must be retired before it can be forgotten",
            )

        runtime_source = next(
            (
                source
                for source in sensor_ingress.stats().sources
                if source.source_id == source_id
            ),
            None,
        )
        if runtime_source is not None and runtime_source.presence != "offline":
            raise HTTPException(
                status_code=409,
                detail="retired sensor must be offline before it can be forgotten",
            )

        try:
            registry.forget(
                source_id,
                expected_state_revision=expected_state_revision,
            )
        except SensorStateRevisionConflict as exc:
            raise_state_revision_conflict(exc)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="sensor is not registered") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        sensor_ingress.forget_source(source_id)
        append_sensor_change(
            kind="forgotten",
            source_id=source_id,
        )
        return {
            "schema": "visionrig/sensor-forget/v2",
            "state_revision": registry.state_revision,
            "status": "forgotten",
            "source_id": source_id,
        }

    @app.patch("/api/v1/sensors/{source_id}/metadata")
    def patch_sensor_metadata(
        source_id: str,
        body: SensorMetadataPatch,
        expected_state_revision: int | None = Query(default=None, ge=0),
    ) -> dict[str, object]:
        previous = registry.get(source_id)
        previous_revision = registry.control_revision(source_id)
        was_registered = registry.contains(source_id)
        try:
            metadata = registry.patch(
                source_id,
                body,
                expected_state_revision=expected_state_revision,
            )
        except SensorStateRevisionConflict as exc:
            raise_state_revision_conflict(exc)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if not was_registered:
            append_sensor_change(
                kind="registered",
                source_id=source_id,
                payload={"metadata": asdict(metadata)},
            )

        metadata_fields_changed = (
            previous.display_name != metadata.display_name
            or previous.location != metadata.location
            or previous.role != metadata.role
        )
        if metadata_fields_changed:
            append_sensor_change(
                kind="metadata_changed",
                source_id=source_id,
                payload={
                    "display_name": metadata.display_name,
                    "location": metadata.location,
                    "role": metadata.role,
                },
            )

        current_revision = registry.control_revision(source_id)
        if (
            previous.enabled != metadata.enabled
            or previous_revision != current_revision
        ):
            append_sensor_change(
                kind="control_changed",
                source_id=source_id,
                payload={
                    "enabled": metadata.enabled,
                    "revision": current_revision,
                    "changed_utc": registry.control_state(source_id).changed_utc,
                },
            )
        return {
            "schema": "visionrig/sensor-metadata/v2",
            "state_revision": registry.state_revision,
            "metadata": asdict(metadata),
        }

    @app.post("/api/v1/sensors/heartbeat", response_model=SensorHeartbeatReceipt)
    def sensor_heartbeat(body: SensorHeartbeat) -> SensorHeartbeatReceipt:
        was_registered = registry.contains(body.source_id)
        previous_discovery = registry.get_discovery(body.source_id)
        previous_runtime = runtime_source_for(body.source_id)
        try:
            registry.observe(
                body.source_id,
                source_type=body.source_type,
                device=body.device,
                capabilities=body.capabilities,
            )
            receipt = sensor_ingress.heartbeat(body)
            emit_registration_changes(
                body.source_id,
                was_registered=was_registered,
                previous_discovery=previous_discovery,
            )
            emit_runtime_change(body.source_id, previous_runtime)
            return receipt
        except SensorIdentityConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/perception/ingest", response_model=PerceptionEvent)
    def ingest(body: IngestBody) -> PerceptionEvent:
        try:
            return runtime.process_direct(
                Frame(
                    source=body.source,
                    sequence=body.frame_sequence,
                    payload=body.payload,
                    dropped_frames=body.dropped_frames,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/frames/ingest", response_model=SensorFrameReceipt)
    async def ingest_sensor_frame(
        request: Request,
        source_id: str = Query(min_length=1, max_length=128),
        source_type: Literal["camera", "screen", "vr", "image"] = Query(),
        frame_sequence: int = Query(ge=0),
        device: str | None = Query(default=None, max_length=256),
        dropped_frames: int = Query(default=0, ge=0),
    ) -> SensorFrameReceipt:
        content_type = request.headers.get("content-type", "")
        payload = await read_bounded_body(request, sensor_ingress.max_payload_bytes)
        was_registered = registry.contains(source_id)
        previous_discovery = registry.get_discovery(source_id)
        try:
            registry.observe(
                source_id,
                source_type=source_type,
                device=device,
            )
            receipt = await run_in_threadpool(
                sensor_ingress.process_encoded,
                source_id=source_id,
                source_type=source_type,
                frame_sequence=frame_sequence,
                payload=payload,
                content_type=content_type,
                device=device,
                dropped_frames=dropped_frames,
            )
            emit_registration_changes(
                source_id,
                was_registered=was_registered,
                previous_discovery=previous_discovery,
            )
            return receipt
        except SensorIdentityConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SensorIngressBusy as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except SensorSequenceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SensorPayloadTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except SensorMediaTypeError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        except SensorDecodeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/perception/events", response_model=EventBatch)
    def events(
        after_cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=64, ge=1, le=256),
    ) -> EventBatch:
        return runtime.events(after_cursor=after_cursor, limit=limit)

    @app.get("/api/v1/world", response_model=WorldSnapshot)
    def get_world() -> WorldSnapshot:
        return runtime.snapshot()

    if modelrig_publisher is not None:
        app.add_event_handler("shutdown", modelrig_publisher.close)

    return app


app = create_app()
