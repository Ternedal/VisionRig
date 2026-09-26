"""Bounded semantic change feed for VisionRig sensor control surfaces."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import os
from pathlib import Path
from threading import Condition, RLock
import tempfile
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError


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


class SensorChangeJournalError(RuntimeError):
    pass


class SensorChangeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["visionrig/sensor-change-event/v1"] = (
        "visionrig/sensor-change-event/v1"
    )
    kind: SensorChangeKind
    source_id: str = Field(min_length=1, max_length=128)
    occurred_utc: str
    state_revision: int = Field(default=0, ge=0)
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


class _SensorChangeJournalFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["visionrig/sensor-change-journal-file/v1"] = (
        "visionrig/sensor-change-journal-file/v1"
    )
    stream_id: str = Field(min_length=1, max_length=128)
    next_cursor: int = Field(ge=1)
    entries: tuple[SensorChangeEntry, ...] = ()


class SensorChangeJournal:
    def __init__(
        self,
        capacity: int = 512,
        *,
        stream_id: str | None = None,
        path: str | Path | None = None,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if stream_id is not None and (not stream_id or len(stream_id) > 128):
            raise ValueError("stream_id must contain 1..128 characters")

        self.path = Path(path).expanduser() if path is not None else None
        self._capacity = capacity
        self._stream_id = stream_id or uuid4().hex
        self._items: deque[SensorChangeEntry] = deque()
        self._next_cursor = 1
        self._lock = RLock()
        self._condition = Condition(self._lock)

        if self.path is not None:
            self._load(stream_id=stream_id)

    @property
    def stream_id(self) -> str:
        return self._stream_id

    @property
    def persistent(self) -> bool:
        return self.path is not None

    def _load(self, *, stream_id: str | None) -> None:
        assert self.path is not None
        if not self.path.exists():
            return
        try:
            state = _SensorChangeJournalFile.model_validate_json(
                self.path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise SensorChangeJournalError(
                f"invalid sensor change journal file: {self.path}"
            ) from exc

        if stream_id is not None and state.stream_id != stream_id:
            raise SensorChangeJournalError(
                "configured sensor change stream_id does not match persisted journal"
            )

        cursors = [entry.cursor for entry in state.entries]
        if cursors != sorted(set(cursors)):
            raise SensorChangeJournalError(
                f"invalid sensor change journal cursor order: {self.path}"
            )
        if cursors and state.next_cursor <= cursors[-1]:
            raise SensorChangeJournalError(
                f"invalid sensor change journal next_cursor: {self.path}"
            )

        self._stream_id = state.stream_id
        retained = state.entries[-self._capacity :]
        self._items = deque(retained)
        self._next_cursor = state.next_cursor

    def _save(self) -> None:
        if self.path is None:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        state = _SensorChangeJournalFile(
            stream_id=self._stream_id,
            next_cursor=self._next_cursor,
            entries=tuple(self._items),
        )
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=self.path.name + ".",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_name = handle.name
                handle.write(state.model_dump_json(indent=2))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
            temp_name = None
        except OSError as exc:
            raise SensorChangeJournalError(
                f"unable to persist sensor change journal: {self.path}"
            ) from exc
        finally:
            if temp_name is not None:
                try:
                    Path(temp_name).unlink(missing_ok=True)
                except OSError:
                    pass

    def append(
        self,
        *,
        kind: SensorChangeKind,
        source_id: str,
        payload: dict[str, Any] | None = None,
        occurred_utc: str | None = None,
        state_revision: int = 0,
    ) -> SensorChangeEntry:
        timestamp = occurred_utc or datetime.now(timezone.utc).isoformat()
        event = SensorChangeEvent(
            kind=kind,
            source_id=source_id,
            occurred_utc=timestamp,
            state_revision=state_revision,
            payload=payload or {},
        )
        with self._condition:
            entry = SensorChangeEntry(cursor=self._next_cursor, event=event)
            previous_items = tuple(self._items)
            previous_next_cursor = self._next_cursor

            self._next_cursor += 1
            self._items.append(entry)
            while len(self._items) > self._capacity:
                self._items.popleft()
            try:
                self._save()
            except Exception:
                self._items = deque(previous_items)
                self._next_cursor = previous_next_cursor
                raise

            self._condition.notify_all()
            return entry

    def wait_for_changes(
        self,
        *,
        after_cursor: int = 0,
        limit: int = 64,
        expected_stream_id: str | None = None,
        wait_seconds: float = 0.0,
    ) -> SensorChangeBatch:
        if wait_seconds < 0 or wait_seconds > 30:
            raise ValueError("wait_seconds must be between 0 and 30")

        with self._condition:
            batch = self.read(
                after_cursor=after_cursor,
                limit=limit,
                expected_stream_id=expected_stream_id,
            )
            if (
                wait_seconds == 0
                or batch.entries
                or batch.gap
                or batch.stream_reset
            ):
                return batch

            self._condition.wait(timeout=wait_seconds)
            return self.read(
                after_cursor=after_cursor,
                limit=limit,
                expected_stream_id=expected_stream_id,
            )

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
