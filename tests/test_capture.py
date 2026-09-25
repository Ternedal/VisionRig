from visionrig.capture import BoundedFrameQueue
from visionrig.contracts import SourceDescriptor
from visionrig.pipeline import Frame


def _frame(sequence: int) -> Frame:
    return Frame(
        source=SourceDescriptor(source_id="cam", source_type="camera"),
        sequence=sequence,
        payload=sequence,
    )


def test_queue_drops_oldest_when_full() -> None:
    queue = BoundedFrameQueue(capacity=2)
    queue.offer(_frame(1))
    queue.offer(_frame(2))
    queue.offer(_frame(3))

    first = queue.take()
    assert first is not None
    assert first.sequence == 2
    assert queue.stats().dropped_total == 1


def test_take_latest_discards_stale_latency() -> None:
    queue = BoundedFrameQueue(capacity=4)
    queue.offer(_frame(1))
    queue.offer(_frame(2))
    queue.offer(_frame(3))

    latest = queue.take_latest()
    assert latest is not None
    assert latest.sequence == 3
    assert latest.dropped_frames == 2
    assert queue.stats().dropped_total == 2
