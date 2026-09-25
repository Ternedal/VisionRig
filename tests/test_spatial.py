from visionrig.contracts import BoundingBox, SourceDescriptor, VisualEntity
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
