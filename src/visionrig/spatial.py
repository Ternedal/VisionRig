"""Bounded image-plane spatial relation inference."""
from __future__ import annotations

from math import hypot

from .contracts import BoundingBox, VisualEntity, VisualRelation
from .pipeline import Frame, StageResult


def _center(box: BoundingBox) -> tuple[float, float]:
    return box.x + box.width / 2.0, box.y + box.height / 2.0


def _area(box: BoundingBox) -> float:
    return box.width * box.height


def _strictly_contains(
    outer: BoundingBox,
    inner: BoundingBox,
    tolerance: float,
) -> bool:
    if _area(outer) <= _area(inner) * 1.01:
        return False
    return (
        inner.x >= outer.x - tolerance
        and inner.y >= outer.y - tolerance
        and inner.x + inner.width <= outer.x + outer.width + tolerance
        and inner.y + inner.height <= outer.y + outer.height + tolerance
    )


def _relation_confidence(
    a: VisualEntity,
    b: VisualEntity,
    geometric: float,
) -> float:
    return float(
        max(
            0.0,
            min(1.0, geometric * min(a.confidence, b.confidence)),
        )
    )


class SpatialRelationStage:
    """Infer conservative 2D relations from bounding-box geometry.

    These relations describe the image plane. They are not 3D/world-space truth.
    """

    name = "spatial_relations"

    def __init__(
        self,
        *,
        max_entities: int = 32,
        max_generated_relations: int = 128,
        near_distance: float = 0.25,
        min_axis_gap: float = 0.015,
        containment_tolerance: float = 0.005,
    ) -> None:
        if not 2 <= max_entities <= 128:
            raise ValueError("max_entities must be between 2 and 128")
        if not 1 <= max_generated_relations <= 1024:
            raise ValueError("max_generated_relations must be between 1 and 1024")
        if not 0.01 <= near_distance <= 1.0:
            raise ValueError("near_distance must be between 0.01 and 1")
        if not 0.0 <= min_axis_gap <= 0.5:
            raise ValueError("min_axis_gap must be between 0 and 0.5")
        if not 0.0 <= containment_tolerance <= 0.1:
            raise ValueError("containment_tolerance must be between 0 and 0.1")

        self._max_entities = max_entities
        self._max_relations = max_generated_relations
        self._near_distance = near_distance
        self._min_axis_gap = min_axis_gap
        self._containment_tolerance = containment_tolerance

    def _pair_relations(
        self,
        a: VisualEntity,
        b: VisualEntity,
    ) -> list[VisualRelation]:
        if a.bbox is None or b.bbox is None:
            return []

        ax, ay = _center(a.bbox)
        bx, by = _center(b.bbox)
        relations: list[VisualRelation] = []

        if a.bbox.x + a.bbox.width <= b.bbox.x:
            gap = b.bbox.x - (a.bbox.x + a.bbox.width)
            if gap >= self._min_axis_gap:
                confidence = _relation_confidence(a, b, min(1.0, 0.65 + gap))
                relations.extend(
                    [
                        VisualRelation(
                            subject_id=a.entity_id,
                            predicate="left_of",
                            object_id=b.entity_id,
                            confidence=confidence,
                        ),
                        VisualRelation(
                            subject_id=b.entity_id,
                            predicate="right_of",
                            object_id=a.entity_id,
                            confidence=confidence,
                        ),
                    ]
                )
        elif b.bbox.x + b.bbox.width <= a.bbox.x:
            gap = a.bbox.x - (b.bbox.x + b.bbox.width)
            if gap >= self._min_axis_gap:
                confidence = _relation_confidence(a, b, min(1.0, 0.65 + gap))
                relations.extend(
                    [
                        VisualRelation(
                            subject_id=b.entity_id,
                            predicate="left_of",
                            object_id=a.entity_id,
                            confidence=confidence,
                        ),
                        VisualRelation(
                            subject_id=a.entity_id,
                            predicate="right_of",
                            object_id=b.entity_id,
                            confidence=confidence,
                        ),
                    ]
                )

        if a.bbox.y + a.bbox.height <= b.bbox.y:
            gap = b.bbox.y - (a.bbox.y + a.bbox.height)
            if gap >= self._min_axis_gap:
                confidence = _relation_confidence(a, b, min(1.0, 0.65 + gap))
                relations.extend(
                    [
                        VisualRelation(
                            subject_id=a.entity_id,
                            predicate="above",
                            object_id=b.entity_id,
                            confidence=confidence,
                        ),
                        VisualRelation(
                            subject_id=b.entity_id,
                            predicate="below",
                            object_id=a.entity_id,
                            confidence=confidence,
                        ),
                    ]
                )
        elif b.bbox.y + b.bbox.height <= a.bbox.y:
            gap = a.bbox.y - (b.bbox.y + b.bbox.height)
            if gap >= self._min_axis_gap:
                confidence = _relation_confidence(a, b, min(1.0, 0.65 + gap))
                relations.extend(
                    [
                        VisualRelation(
                            subject_id=b.entity_id,
                            predicate="above",
                            object_id=a.entity_id,
                            confidence=confidence,
                        ),
                        VisualRelation(
                            subject_id=a.entity_id,
                            predicate="below",
                            object_id=b.entity_id,
                            confidence=confidence,
                        ),
                    ]
                )

        distance = hypot(ax - bx, ay - by)
        if distance <= self._near_distance:
            geometric = max(0.35, 1.0 - distance / self._near_distance)
            confidence = _relation_confidence(a, b, geometric)
            relations.extend(
                [
                    VisualRelation(
                        subject_id=a.entity_id,
                        predicate="near",
                        object_id=b.entity_id,
                        confidence=confidence,
                    ),
                    VisualRelation(
                        subject_id=b.entity_id,
                        predicate="near",
                        object_id=a.entity_id,
                        confidence=confidence,
                    ),
                ]
            )

        if _strictly_contains(b.bbox, a.bbox, self._containment_tolerance):
            relations.append(
                VisualRelation(
                    subject_id=a.entity_id,
                    predicate="inside",
                    object_id=b.entity_id,
                    confidence=_relation_confidence(a, b, 0.9),
                )
            )
        if _strictly_contains(a.bbox, b.bbox, self._containment_tolerance):
            relations.append(
                VisualRelation(
                    subject_id=b.entity_id,
                    predicate="inside",
                    object_id=a.entity_id,
                    confidence=_relation_confidence(a, b, 0.9),
                )
            )

        return relations

    def process(self, frame: Frame, current: StageResult) -> StageResult:
        candidates = [entity for entity in current.entities if entity.bbox is not None]
        candidates.sort(key=lambda item: (-item.confidence, item.entity_id))
        candidates = candidates[: self._max_entities]

        existing = {
            (relation.subject_id, relation.predicate, relation.object_id)
            for relation in current.relations
        }
        generated: list[VisualRelation] = []

        for index, first in enumerate(candidates):
            for second in candidates[index + 1:]:
                for relation in self._pair_relations(first, second):
                    key = (
                        relation.subject_id,
                        relation.predicate,
                        relation.object_id,
                    )
                    if key in existing:
                        continue
                    existing.add(key)
                    generated.append(relation)

        generated.sort(
            key=lambda item: (
                -item.confidence,
                item.subject_id,
                item.predicate,
                item.object_id,
            )
        )
        generated = generated[: self._max_relations]

        return StageResult(
            entities=current.entities,
            relations=current.relations + tuple(generated),
            landmarks=current.landmarks,
            depth=current.depth,
            scene_label=current.scene_label,
            scene_confidence=current.scene_confidence,
        )
