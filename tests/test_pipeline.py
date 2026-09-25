import pytest

from visionrig.contracts import SourceDescriptor
from visionrig.pipeline import Frame, PassthroughStage, PerceptionPipeline
from visionrig.world import VisualWorld


def test_passthrough_pipeline_emits_typed_event() -> None:
    source = SourceDescriptor(source_id="synthetic-1", source_type="synthetic")
    pipeline = PerceptionPipeline((PassthroughStage(),))
    event = pipeline.process(
        Frame(
            source=source,
            sequence=1,
            payload={
                "scene_label": "office",
                "scene_confidence": 0.9,
                "entities": [{
                    "entity_id": "obj-1",
                    "kind": "object",
                    "label": "coffee_cup",
                    "confidence": 0.95,
                }],
            },
        )
    )
    assert event.scene_label == "office"
    assert event.entities[0].label == "coffee_cup"


def test_world_rejects_stale_sequence() -> None:
    source = SourceDescriptor(source_id="synthetic-1", source_type="synthetic")
    pipeline = PerceptionPipeline()
    world = VisualWorld()
    world.apply(pipeline.process(Frame(source=source, sequence=2, payload=None)))
    with pytest.raises(ValueError):
        world.apply(pipeline.process(Frame(source=source, sequence=2, payload=None)))
