"""Composable perception pipeline.

Core intentionally has no OpenCV/CUDA dependency. Adapters implement Stage and
can be loaded by deployment-specific packages later.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from .contracts import (
    DepthObservation,
    LandmarkObservation,
    PerceptionEvent,
    SourceDescriptor,
    VisualEntity,
    VisualRelation,
)


@dataclass(frozen=True, slots=True)
class Frame:
    source: SourceDescriptor
    sequence: int
    payload: Any
    dropped_frames: int = 0
    sensor_data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StageResult:
    entities: tuple[VisualEntity, ...] = ()
    relations: tuple[VisualRelation, ...] = ()
    landmarks: tuple[LandmarkObservation, ...] = ()
    depth: tuple[DepthObservation, ...] = ()
    scene_label: str | None = None
    scene_confidence: float | None = None


class Stage(Protocol):
    name: str

    def process(self, frame: Frame, current: StageResult) -> StageResult: ...


class PerceptionPipeline:
    def __init__(self, stages: tuple[Stage, ...] = ()) -> None:
        names = [stage.name for stage in stages]
        if len(names) != len(set(names)):
            raise ValueError("stage names must be unique")
        self._stages = stages

    @property
    def stages(self) -> tuple[str, ...]:
        return tuple(stage.name for stage in self._stages)

    def process(self, frame: Frame) -> PerceptionEvent:
        result = StageResult()
        for stage in self._stages:
            result = stage.process(frame, result)

        return PerceptionEvent.now(
            event_id=f"evt-{uuid4()}",
            source=frame.source,
            frame_sequence=frame.sequence,
            entities=result.entities,
            relations=result.relations,
            landmarks=result.landmarks,
            depth=result.depth,
            scene_label=result.scene_label,
            scene_confidence=result.scene_confidence,
            dropped_frames=frame.dropped_frames,
        )


class PassthroughStage:
    """Development stage that turns already-structured payloads into observations."""

    name = "passthrough"

    def process(self, frame: Frame, current: StageResult) -> StageResult:
        if not isinstance(frame.payload, dict):
            return current

        entities = tuple(
            item if isinstance(item, VisualEntity) else VisualEntity.model_validate(item)
            for item in frame.payload.get("entities", ())
        )
        relations = tuple(
            item if isinstance(item, VisualRelation) else VisualRelation.model_validate(item)
            for item in frame.payload.get("relations", ())
        )
        landmarks = tuple(
            item if isinstance(item, LandmarkObservation) else LandmarkObservation.model_validate(item)
            for item in frame.payload.get("landmarks", ())
        )
        depth = tuple(
            item if isinstance(item, DepthObservation) else DepthObservation.model_validate(item)
            for item in frame.payload.get("depth", ())
        )
        return StageResult(
            entities=current.entities + entities,
            relations=current.relations + relations,
            landmarks=current.landmarks + landmarks,
            depth=current.depth + depth,
            scene_label=frame.payload.get("scene_label", current.scene_label),
            scene_confidence=frame.payload.get("scene_confidence", current.scene_confidence),
        )
