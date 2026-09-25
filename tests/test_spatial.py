from visionrig.contracts import BoundingBox, DepthObservation, SourceDescriptor, VisualEntity
from visionrig.pipeline import Frame, StageResult
from visionrig.spatial import SpatialRelationStage


FRAME = Frame(
    source=SourceDescriptor(source_id="test", source_type="synthetic"),
    sequence=1,
    payload=None,
)


def entity(
    entity_id: str,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    confidence: float = 1.0,
) -> VisualEntity:
    return VisualEntity(
        entity_id=entity_id,
        kind="object",
        label=entity_id,
        confidence=confidence,
        bbox=BoundingBox(x=x, y=y, width=width, height=height),
    )


def relation_keys(result: StageResult):
    return {
        (item.subject_id, item.predicate, item.object_id)
        for item in result.relations
    }


def test_spatial_stage_emits_directional_axis_relations() -> None:
    left = entity("left", x=0.05, y=0.10, width=0.10, height=0.10)
    right = entity("right", x=0.45, y=0.55, width=0.10, height=0.10)

    result = SpatialRelationStage().process(
        FRAME,
        StageResult(entities=(left, right)),
    )
    keys = relation_keys(result)
    assert ("left", "left_of", "right") in keys
    assert ("right", "right_of", "left") in keys
    assert ("left", "above", "right") in keys
    assert ("right", "below", "left") in keys


def test_spatial_stage_emits_near_symmetrically() -> None:
    a = entity("a", x=0.10, y=0.10, width=0.10, height=0.10)
    b = entity("b", x=0.20, y=0.12, width=0.10, height=0.10)
    result = SpatialRelationStage(near_distance=0.25).process(
        FRAME,
        StageResult(entities=(a, b)),
    )
    keys = relation_keys(result)
    assert ("a", "near", "b") in keys
    assert ("b", "near", "a") in keys


def test_spatial_stage_emits_strict_containment_only() -> None:
    outer = entity("outer", x=0.10, y=0.10, width=0.70, height=0.70)
    inner = entity("inner", x=0.20, y=0.25, width=0.10, height=0.10)
    result = SpatialRelationStage().process(
        FRAME,
        StageResult(entities=(outer, inner)),
    )
    keys = relation_keys(result)
    assert ("inner", "inside", "outer") in keys
    assert ("outer", "inside", "inner") not in keys

    same = entity("same", x=0.10, y=0.10, width=0.70, height=0.70)
    identical = SpatialRelationStage().process(
        FRAME,
        StageResult(entities=(outer, same)),
    )
    assert ("outer", "inside", "same") not in relation_keys(identical)
    assert ("same", "inside", "outer") not in relation_keys(identical)


def test_spatial_stage_is_bounded() -> None:
    entities = tuple(
        entity(
            f"e{i:02d}",
            x=(i % 6) * 0.15,
            y=(i // 6) * 0.15,
            width=0.05,
            height=0.05,
        )
        for i in range(12)
    )
    stage = SpatialRelationStage(max_generated_relations=5)
    result = stage.process(FRAME, StageResult(entities=entities))
    assert len(result.relations) <= 5


def test_spatial_stage_uses_metric_depth_for_front_behind() -> None:
    front = entity("front", x=0.10, y=0.10, width=0.20, height=0.20, confidence=0.9)
    back = entity("back", x=0.12, y=0.12, width=0.20, height=0.20, confidence=0.8)
    depth = (
        DepthObservation(
            subject_entity_id="front",
            relative_depth=0.2,
            distance_m=1.25,
            confidence=0.95,
            method="hardware-depth",
        ),
        DepthObservation(
            subject_entity_id="back",
            relative_depth=0.7,
            distance_m=2.10,
            confidence=0.90,
            method="hardware-depth",
        ),
    )

    result = SpatialRelationStage(min_depth_gap_m=0.20).process(
        FRAME,
        StageResult(entities=(front, back), depth=depth),
    )
    keys = relation_keys(result)
    assert ("front", "in_front_of", "back") in keys
    assert ("back", "behind", "front") in keys


def test_spatial_stage_does_not_infer_depth_order_from_relative_depth_only() -> None:
    first = entity("first", x=0.10, y=0.10, width=0.20, height=0.20)
    second = entity("second", x=0.12, y=0.12, width=0.20, height=0.20)
    depth = (
        DepthObservation(
            subject_entity_id="first",
            relative_depth=0.1,
            method="onnx-monocular-relative",
        ),
        DepthObservation(
            subject_entity_id="second",
            relative_depth=0.9,
            method="onnx-monocular-relative",
        ),
    )

    result = SpatialRelationStage().process(
        FRAME,
        StageResult(entities=(first, second), depth=depth),
    )
    keys = relation_keys(result)
    assert ("first", "in_front_of", "second") not in keys
    assert ("second", "behind", "first") not in keys
