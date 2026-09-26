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
from .sensor_registry import SensorMetadataPatch, SensorRegistry


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

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "visionrig",
            "schema": "visionrig/health/v7",
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
                "schema": "visionrig/sensor-registry/v1",
                "entries": len(registry.list()),
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

    @app.get("/api/v1/sensors/catalog")
    def sensor_catalog() -> dict[str, object]:
        runtime_status = sensor_ingress.stats()
        runtime_by_id = {source.source_id: source for source in runtime_status.sources}
        metadata_by_id = {entry.source_id: entry for entry in registry.list()}
        source_ids = sorted(set(runtime_by_id) | set(metadata_by_id))
        return {
            "schema": "visionrig/sensor-catalog/v1",
            "sources": [
                {
                    "source_id": source_id,
                    "runtime": (
                        asdict(runtime_by_id[source_id])
                        if source_id in runtime_by_id
                        else None
                    ),
                    "metadata": asdict(registry.get(source_id)),
                }
                for source_id in source_ids
            ],
        }

    @app.patch("/api/v1/sensors/{source_id}/metadata")
    def patch_sensor_metadata(
        source_id: str,
        body: SensorMetadataPatch,
    ) -> dict[str, object]:
        try:
            metadata = registry.patch(source_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "schema": "visionrig/sensor-metadata/v1",
            "metadata": asdict(metadata),
        }

    @app.post("/api/v1/sensors/heartbeat", response_model=SensorHeartbeatReceipt)
    def sensor_heartbeat(body: SensorHeartbeat) -> SensorHeartbeatReceipt:
        try:
            return sensor_ingress.heartbeat(body)
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
        try:
            return await run_in_threadpool(
                sensor_ingress.process_encoded,
                source_id=source_id,
                source_type=source_type,
                frame_sequence=frame_sequence,
                payload=payload,
                content_type=content_type,
                device=device,
                dropped_frames=dropped_frames,
            )
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
