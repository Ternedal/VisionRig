"""Ephemeral visual world snapshot.

This state is intentionally bounded and non-durable. ModelRig/Consciousness Core
remains authoritative for semantic world state and memory.
"""
from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock

from .contracts import PerceptionEvent, WorldSnapshot


class VisualWorld:
    def __init__(self) -> None:
        self._lock = RLock()
        self._last: PerceptionEvent | None = None

    def apply(self, event: PerceptionEvent) -> WorldSnapshot:
        with self._lock:
            if self._last is not None:
                if event.source.source_id == self._last.source.source_id:
                    if event.frame_sequence <= self._last.frame_sequence:
                        raise ValueError("frame_sequence must increase monotonically per source")
            self._last = event
            return self.snapshot()

    def snapshot(self) -> WorldSnapshot:
        with self._lock:
            event = self._last
            return WorldSnapshot(
                generated_at=datetime.now(timezone.utc),
                last_event_id=event.event_id if event else None,
                source_id=event.source.source_id if event else None,
                entities=event.entities if event else (),
                relations=event.relations if event else (),
                landmarks=event.landmarks if event else (),
                depth=event.depth if event else (),
                scene_label=event.scene_label if event else None,
            )
