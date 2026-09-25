"""HTTP surface for the standalone VisionRig service."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .capabilities import probe_capabilities
from .contracts import PerceptionEvent, SourceDescriptor, WorldSnapshot
from .pipeline import Frame, PassthroughStage, PerceptionPipeline
from .runtime import VisionRuntime


class IngestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: SourceDescriptor
    frame_sequence: int = Field(ge=0)
    payload: dict
    dropped_frames: int = Field(default=0, ge=0)


def create_app() -> FastAPI:
    app = FastAPI(title="VisionRig", version="0.2.0")
    pipeline = PerceptionPipeline((PassthroughStage(),))
    runtime = VisionRuntime(pipeline)

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "visionrig",
            "schema": "visionrig/health/v1",
            "stages": pipeline.stages,
            "capture_queue": runtime.stats().__dict__,
        }

    @app.get("/api/v1/capabilities")
    def capabilities() -> dict[str, object]:
        return probe_capabilities()

    @app.get("/api/v1/capture/stats")
    def capture_stats() -> dict[str, int]:
        return runtime.stats().__dict__

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

    @app.get("/api/v1/world", response_model=WorldSnapshot)
    def get_world() -> WorldSnapshot:
        return runtime.snapshot()

    return app


app = create_app()
