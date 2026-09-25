"""Runtime joining capture transport, pipeline, world state and event journal."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .capture import BoundedFrameQueue, QueueStats
from .contracts import PerceptionEvent, WorldSnapshot
from .journal import EventBatch, EventJournal
from .pipeline import Frame, PerceptionPipeline
from .world import VisualWorld


class PerceptionEventSink(Protocol):
    def accept(self, event: PerceptionEvent) -> None: ...


@dataclass(frozen=True, slots=True)
class SinkDispatchStats:
    configured: int
    dispatched_total: int
    failed_total: int


class VisionRuntime:
    def __init__(
        self,
        pipeline: PerceptionPipeline,
        *,
        queue_capacity: int = 4,
        journal_capacity: int = 256,
        world: VisualWorld | None = None,
        journal: EventJournal | None = None,
        event_sinks: tuple[PerceptionEventSink, ...] = (),
    ) -> None:
        self.pipeline = pipeline
        self.queue = BoundedFrameQueue(queue_capacity)
        self.world = world or VisualWorld()
        self.journal = journal or EventJournal(journal_capacity)
        self._event_sinks = event_sinks
        self._sink_dispatched_total = 0
        self._sink_failed_total = 0

    def submit(self, frame: Frame) -> QueueStats:
        self.queue.offer(frame)
        return self.queue.stats()

    def _accept(self, frame: Frame) -> PerceptionEvent:
        event = self.pipeline.process(frame)
        self.world.apply(event)
        self.journal.append(event)
        for sink in self._event_sinks:
            self._sink_dispatched_total += 1
            try:
                sink.accept(event)
            except Exception:
                # Perception remains authoritative for its own local runtime.
                # Optional downstream integrations are observable but cannot
                # turn a completed local perception into a failed frame.
                self._sink_failed_total += 1
        return event

    def process_next(self, *, freshest: bool = True) -> PerceptionEvent | None:
        frame = self.queue.take_latest() if freshest else self.queue.take(0)
        if frame is None:
            return None
        return self._accept(frame)

    def process_direct(self, frame: Frame) -> PerceptionEvent:
        return self._accept(frame)

    def snapshot(self) -> WorldSnapshot:
        return self.world.snapshot()

    def events(self, *, after_cursor: int = 0, limit: int = 64) -> EventBatch:
        return self.journal.read(after_cursor=after_cursor, limit=limit)

    def stats(self) -> QueueStats:
        return self.queue.stats()

    def sink_stats(self) -> SinkDispatchStats:
        return SinkDispatchStats(
            configured=len(self._event_sinks),
            dispatched_total=self._sink_dispatched_total,
            failed_total=self._sink_failed_total,
        )
