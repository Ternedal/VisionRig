"""Bounded frame transport for live visual sources."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from threading import Condition

from .pipeline import Frame


@dataclass(frozen=True, slots=True)
class QueueStats:
    capacity: int
    depth: int
    accepted_total: int
    dropped_total: int


class BoundedFrameQueue:
    """A small live-video queue that prefers fresh frames over stale latency.

    When full, the oldest frame is discarded. The accepted replacement carries
    an incremented dropped_frames count so downstream observations can expose
    that loss instead of silently pretending every source frame was processed.
    """

    def __init__(self, capacity: int = 4) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._items: deque[Frame] = deque()
        self._accepted_total = 0
        self._dropped_total = 0
        self._condition = Condition()

    def offer(self, frame: Frame) -> None:
        with self._condition:
            dropped_now = 0
            if len(self._items) >= self._capacity:
                self._items.popleft()
                self._dropped_total += 1
                dropped_now = 1
            if dropped_now:
                frame = replace(frame, dropped_frames=frame.dropped_frames + dropped_now)
            self._items.append(frame)
            self._accepted_total += 1
            self._condition.notify()

    def take(self, timeout: float | None = None) -> Frame | None:
        with self._condition:
            if not self._items:
                self._condition.wait(timeout)
            if not self._items:
                return None
            return self._items.popleft()

    def take_latest(self) -> Frame | None:
        """Return the freshest queued frame and account for skipped stale frames."""
        with self._condition:
            if not self._items:
                return None
            latest = self._items.pop()
            skipped = len(self._items)
            if skipped:
                self._items.clear()
                self._dropped_total += skipped
                latest = replace(
                    latest,
                    dropped_frames=latest.dropped_frames + skipped,
                )
            return latest

    def stats(self) -> QueueStats:
        with self._condition:
            return QueueStats(
                capacity=self._capacity,
                depth=len(self._items),
                accepted_total=self._accepted_total,
                dropped_total=self._dropped_total,
            )
