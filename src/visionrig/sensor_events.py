"""Bounded semantic change feed for VisionRig sensor control surfaces."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


SensorChangeKind = Literal[
    "registered",
    "discovery_changed",
    "metadata_changed",
    "control_changed",
    "retired",
    "restored",
    "forgotten",
    "runtime_changed",
]


class SensorChangeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["visionrig/sensor-change-event/v1"] = (
        "visionrig/sensor-change-event/v1"
    )
    kind: SensorChangeKind
    source_id: str = Field(min_length=1, max_length=128)
    occurred_utc: str
    payload: dict[str, Any] = Field(default_factory=dict)


class SensorChangeEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cursor: int = Field(ge=1)
    event: SensorChangeEvent


class SensorChangeBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["visionrig/sensor-change-batch/v2"] = (
        "visionrig/sensor-change-batch/v2"
    )
    stream_id: str = Field(min_length=1, max_length=128)
    entries: tuple[SensorChangeEntry, ...]
    next_cursor: int = Field(ge=0)
    oldest_available_cursor: int | None = Field(default=None, ge=1)
    newest_available_cursor: int | None = Field(default=None, ge=1)
    gap: bool = False
    stream_reset: bool = False


class SensorChangeJournal:
    def __init__(
        self,
        capacity: int = 512,
        *,
        stream_id: str | None = None,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        selected_stream_id = stream_id or uuid4().hex
        if not selected_stream_id or len(selected_stream_id) > 128:
            raise ValueError("stream_id must contain 1..128 characters")
        self._stream_id = selected_stream_id
        self._capacity = capacity
        self._items: deque[SensorChangeEntry] = deque()
        self._next_cursor = 1
        self._lock = RLock()

    @property
    def stream_id(self) -> str:
        return self._stream_id

    def append(
        self,
        *,
        kind: SensorChangeKind,
        source_id: str,
        payload: dict[str, Any] | None = None,
        occurred_utc: str | None = None,
    ) -> SensorChangeEntry:
        timestamp = occurred_utc or datetime.now(timezone.utc).isoformat()
        event = SensorChangeEvent(
            kind=kind,
            source_id=source_id,
            occurred_utc=timestamp,
            payload=payload or {},
        )
        with self._lock:
            entry = SensorChangeEntry(cursor=self._next_cursor, event=event)
            self._next_cursor += 1
            self._items.append(entry)
            while len(self._items) > self._capacity:
                self._items.popleft()
            return entry

    def read(
        self,
        *,
        after_cursor: int = 0,
        limit: int = 64,
        expected_stream_id: str | None = None,
    ) -> SensorChangeBatch:
        if after_cursor < 0:
            raise ValueError("after_cursor must be >= 0")
        if not 1 <= limit <= 256:
            raise ValueError("limit must be between 1 and 256")
        with self._lock:
            if (
                expected_stream_id is not None
                and expected_stream_id != self._stream_id
            ):
                return SensorChangeBatch(
                    stream_id=self._stream_id,
                    entries=(),
                    next_cursor=0,
                    oldest_available_cursor=(
                        self._items[0].cursor if self._items else None
                    ),
                    newest_available_cursor=(
                        self._items[-1].cursor if self._items else None
                    ),
                    gap=False,
                    stream_reset=True,
                )
            oldest = self._items[0].cursor if self._items else None
            newest = self._items[-1].cursor if self._items else None
            gap = bool(
                oldest is not None
                and after_cursor != 0
                and after_cursor < oldest - 1
            )
            effective_after = (
                oldest - 1
                if gap and oldest is not None
                else after_cursor
            )
            entries = tuple(
                entry
                for entry in self._items
                if entry.cursor > effective_after
            )[:limit]
            next_cursor = entries[-1].cursor if entries else after_cursor
            if gap and not entries and oldest is not None:
                next_cursor = oldest - 1
            return SensorChangeBatch(
                stream_id=self._stream_id,
                entries=entries,
                next_cursor=next_cursor,
                oldest_available_cursor=oldest,
                newest_available_cursor=newest,
                gap=gap,
            )
