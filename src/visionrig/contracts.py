"""Strict public contracts for VisionRig.

These are observation contracts. They intentionally carry confidence and
provenance and do not grant memory, cognition, identity, or action authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

UnitInterval = Annotated[float, Field(ge=0.0, le=1.0, strict=True, allow_inf_nan=False)]
SignedUnit = Annotated[float, Field(ge=-1.0, le=1.0, strict=True, allow_inf_nan=False)]


class BoundingBox(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    x: UnitInterval
    y: UnitInterval
    width: UnitInterval
    height: UnitInterval


class VisualEntity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    entity_id: str = Field(min_length=1, max_length=128)
    kind: Literal["person", "face", "object", "text", "hand", "body", "unknown"]
    label: str = Field(min_length=1, max_length=512)
    confidence: UnitInterval
    bbox: BoundingBox | None = None
    track_id: str | None = Field(default=None, max_length=128)
    identity_hint: str | None = Field(default=None, max_length=256)


class VisualRelation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    subject_id: str = Field(min_length=1, max_length=128)
    predicate: Literal[
        "left_of", "right_of", "above", "below", "near", "inside",
        "looking_at", "holding", "moving_towards", "moving_away"
    ]
    object_id: str = Field(min_length=1, max_length=128)
    confidence: UnitInterval


class VisualLandmark(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=128)
    x: UnitInterval
    y: UnitInterval
    z: SignedUnit | None = None
    confidence: UnitInterval


class LandmarkObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    observation_id: str = Field(min_length=1, max_length=128)
    group: Literal["pose", "left_hand", "right_hand", "face"]
    subject_entity_id: str | None = Field(default=None, max_length=128)
    landmarks: tuple[VisualLandmark, ...] = Field(min_length=1)


class DepthObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    subject_entity_id: str = Field(min_length=1, max_length=128)
    relative_depth: UnitInterval
    confidence: UnitInterval | None = None
    method: str = Field(min_length=1, max_length=128)


class SourceDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str = Field(min_length=1, max_length=128)
    source_type: Literal["camera", "screen", "vr", "image", "video", "synthetic"]
    device: str | None = Field(default=None, max_length=256)


class PerceptionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_id: Literal["visionrig/perception-event/v2"] = "visionrig/perception-event/v2"
    event_id: str = Field(min_length=1, max_length=128)
    observed_at: datetime
    source: SourceDescriptor
    frame_sequence: int = Field(ge=0)
    entities: tuple[VisualEntity, ...] = ()
    relations: tuple[VisualRelation, ...] = ()
    landmarks: tuple[LandmarkObservation, ...] = ()
    depth: tuple[DepthObservation, ...] = ()
    scene_label: str | None = Field(default=None, max_length=256)
    scene_confidence: UnitInterval | None = None
    dropped_frames: int = Field(default=0, ge=0)
    production_authority: Literal[False] = False

    @classmethod
    def now(
        cls,
        *,
        event_id: str,
        source: SourceDescriptor,
        frame_sequence: int,
        entities: tuple[VisualEntity, ...] = (),
        relations: tuple[VisualRelation, ...] = (),
        landmarks: tuple[LandmarkObservation, ...] = (),
        depth: tuple[DepthObservation, ...] = (),
        scene_label: str | None = None,
        scene_confidence: float | None = None,
        dropped_frames: int = 0,
    ) -> "PerceptionEvent":
        return cls(
            event_id=event_id,
            observed_at=datetime.now(timezone.utc),
            source=source,
            frame_sequence=frame_sequence,
            entities=entities,
            relations=relations,
            landmarks=landmarks,
            depth=depth,
            scene_label=scene_label,
            scene_confidence=scene_confidence,
            dropped_frames=dropped_frames,
        )


class WorldSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_id: Literal["visionrig/world-snapshot/v2"] = "visionrig/world-snapshot/v2"
    generated_at: datetime
    last_event_id: str | None = None
    source_id: str | None = None
    entities: tuple[VisualEntity, ...] = ()
    relations: tuple[VisualRelation, ...] = ()
    landmarks: tuple[LandmarkObservation, ...] = ()
    depth: tuple[DepthObservation, ...] = ()
    scene_label: str | None = None
