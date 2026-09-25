"""Bounded, non-durable perception event journal for downstream consumers."""
from __future__ import annotations

from collections import deque
from threading import RLock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import PerceptionEvent


class JournalEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    cursor: int = Field(ge=1)
    event: PerceptionEvent


class EventBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_id: Literal["visionrig/event-batch/v1"] = "visionrig/event-batch/v1"
    entries: tuple[JournalEntry, ...]
    next_cursor: int = Field(ge=0)
    oldest_available_cursor: int | None = Field(default=None, ge=1)
    newest_available_cursor: int | None = Field(default=None, ge=1)
    gap: bool = False


class EventJournal:
    def __init__(self, capacity: int = 256) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._items: deque[JournalEntry] = deque()
        self._next_cursor = 1
        self._lock = RLock()

    def append(self, event: PerceptionEvent) -> JournalEntry:
        with self._lock:
            entry = JournalEntry(cursor=self._next_cursor, event=event)
            self._next_cursor += 1
            self._items.append(entry)
            while len(self._items) > self._capacity:
                self._items.popleft()
            return entry

    def read(self, *, after_cursor: int = 0, limit: int = 64) -> EventBatch:
        if after_cursor < 0:
            raise ValueError("after_cursor must be >= 0")
        if not 1 <= limit <= 256:
            raise ValueError("limit must be between 1 and 256")
        with self._lock:
            oldest = self._items[0].cursor if self._items else None
            newest = self._items[-1].cursor if self._items else None
            gap = bool(oldest is not None and after_cursor != 0 and after_cursor < oldest - 1)
            effective_after = oldest - 1 if gap and oldest is not None else after_cursor
            entries = tuple(
                entry
                for entry in self._items
                if entry.cursor > effective_after
            )[:limit]
            next_cursor = entries[-1].cursor if entries else after_cursor
            if gap and not entries and oldest is not None:
                next_cursor = oldest - 1
            return EventBatch(
                entries=entries,
                next_cursor=next_cursor,
                oldest_available_cursor=oldest,
                newest_available_cursor=newest,
                gap=gap,
            )
