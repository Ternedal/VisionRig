from visionrig.contracts import SourceDescriptor
from visionrig.pipeline import Frame, PassthroughStage, PerceptionPipeline
from visionrig.runtime import VisionRuntime


def test_runtime_prefers_freshest_frame_and_exposes_drop_count() -> None:
    source = SourceDescriptor(source_id="cam", source_type="camera")
    runtime = VisionRuntime(
        PerceptionPipeline((PassthroughStage(),)),
        queue_capacity=4,
    )

    for sequence in range(3):
        runtime.submit(
            Frame(
                source=source,
                sequence=sequence,
                payload={"scene_label": f"frame-{sequence}"},
            )
        )

    event = runtime.process_next(freshest=True)
    assert event is not None
    assert event.frame_sequence == 2
    assert event.scene_label == "frame-2"
    assert event.dropped_frames == 2
    assert runtime.snapshot().last_event_id == event.event_id
