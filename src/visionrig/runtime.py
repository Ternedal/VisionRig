"""Small synchronous runtime joining capture transport, pipeline and visual world."""
from __future__ import annotations

from .capture import BoundedFrameQueue, QueueStats
from .contracts import PerceptionEvent, WorldSnapshot
from .pipeline import Frame, PerceptionPipeline
from .world import VisualWorld


class VisionRuntime:
    def __init__(
        self,
        pipeline: PerceptionPipeline,
        *,
        queue_capacity: int = 4,
        world: VisualWorld | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.queue = BoundedFrameQueue(queue_capacity)
        self.world = world or VisualWorld()

    def submit(self, frame: Frame) -> QueueStats:
        self.queue.offer(frame)
        return self.queue.stats()

    def process_next(self, *, freshest: bool = True) -> PerceptionEvent | None:
        frame = self.queue.take_latest() if freshest else self.queue.take(0)
        if frame is None:
            return None
        event = self.pipeline.process(frame)
        self.world.apply(event)
        return event

    def process_direct(self, frame: Frame) -> PerceptionEvent:
        event = self.pipeline.process(frame)
        self.world.apply(event)
        return event

    def snapshot(self) -> WorldSnapshot:
        return self.world.snapshot()

    def stats(self) -> QueueStats:
        return self.queue.stats()
