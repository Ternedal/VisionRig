from visionrig.contracts import BoundingBox, SourceDescriptor, VisualEntity
from visionrig.pipeline import Frame, StageResult
from visionrig.tracking import IoUTrackingStage


def _entity(x: float) -> VisualEntity:
    return VisualEntity(
        entity_id=f"e-{x}",
        kind="person",
        label="person",
        confidence=0.9,
        bbox=BoundingBox(x=x, y=0.1, width=0.2, height=0.4),
    )


def test_tracker_preserves_track_for_overlapping_entity() -> None:
    stage = IoUTrackingStage(iou_threshold=0.2)
    source = SourceDescriptor(source_id="camera", source_type="camera")

    first = stage.process(
        Frame(source=source, sequence=1, payload=None),
        StageResult(entities=(_entity(0.10),)),
    )
    second = stage.process(
        Frame(source=source, sequence=2, payload=None),
        StageResult(entities=(_entity(0.12),)),
    )

    assert first.entities[0].track_id is not None
    assert second.entities[0].track_id == first.entities[0].track_id


def test_track_is_not_shared_across_sources() -> None:
    stage = IoUTrackingStage(iou_threshold=0.2)
    a = SourceDescriptor(source_id="a", source_type="camera")
    b = SourceDescriptor(source_id="b", source_type="camera")

    first = stage.process(
        Frame(source=a, sequence=1, payload=None),
        StageResult(entities=(_entity(0.10),)),
    )
    second = stage.process(
        Frame(source=b, sequence=1, payload=None),
        StageResult(entities=(_entity(0.10),)),
    )

    assert first.entities[0].track_id != second.entities[0].track_id
