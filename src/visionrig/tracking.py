"""Dependency-free short-term entity tracking for perception continuity."""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import BoundingBox, VisualEntity
from .pipeline import Frame, StageResult


def _iou(a: BoundingBox, b: BoundingBox) -> float:
    ax2, ay2 = a.x + a.width, a.y + a.height
    bx2, by2 = b.x + b.width, b.y + b.height
    ix1, iy1 = max(a.x, b.x), max(a.y, b.y)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = (a.width * a.height) + (b.width * b.height) - inter
    return inter / union if union > 0.0 else 0.0


@dataclass(slots=True)
class _Track:
    track_id: str
    label: str
    kind: str
    bbox: BoundingBox
    last_sequence: int


class IoUTrackingStage:
    """Greedy per-source tracker; track ids are not durable identities."""

    name = "iou_tracking"

    def __init__(self, *, iou_threshold: float = 0.3, max_age_frames: int = 30) -> None:
        if not 0.0 <= iou_threshold <= 1.0:
            raise ValueError("iou_threshold must be between 0 and 1")
        if max_age_frames < 1:
            raise ValueError("max_age_frames must be >= 1")
        self._threshold = iou_threshold
        self._max_age = max_age_frames
        self._next_id = 1
        self._tracks: dict[str, dict[str, _Track]] = {}

    def process(self, frame: Frame, current: StageResult) -> StageResult:
        source_tracks = self._tracks.setdefault(frame.source.source_id, {})
        stale = [
            track_id
            for track_id, track in source_tracks.items()
            if frame.sequence - track.last_sequence > self._max_age
        ]
        for track_id in stale:
            source_tracks.pop(track_id, None)

        used_tracks: set[str] = set()
        entities: list[VisualEntity] = []

        for entity in current.entities:
            if entity.bbox is None:
                entities.append(entity)
                continue

            best: _Track | None = None
            best_iou = self._threshold
            for track in source_tracks.values():
                if track.track_id in used_tracks:
                    continue
                if track.label != entity.label or track.kind != entity.kind:
                    continue
                score = _iou(track.bbox, entity.bbox)
                if score >= best_iou:
                    best = track
                    best_iou = score

            if best is None:
                track_id = f"trk-{self._next_id:08d}"
                self._next_id += 1
                best = _Track(
                    track_id=track_id,
                    label=entity.label,
                    kind=entity.kind,
                    bbox=entity.bbox,
                    last_sequence=frame.sequence,
                )
                source_tracks[track_id] = best
            else:
                best.bbox = entity.bbox
                best.last_sequence = frame.sequence

            used_tracks.add(best.track_id)
            entities.append(entity.model_copy(update={"track_id": best.track_id}))

        return StageResult(
            entities=tuple(entities),
            relations=current.relations,
            landmarks=current.landmarks,
            depth=current.depth,
            scene_label=current.scene_label,
            scene_confidence=current.scene_confidence,
        )
