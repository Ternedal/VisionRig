"""Crash-safe persistent sequence/drop state for reference sensor producers."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from threading import RLock
import tempfile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ProducerStateError(RuntimeError):
    pass


class ProducerStateEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    next_sequence: int = Field(default=0, ge=0)
    pending_dropped: int = Field(default=0, ge=0)
    inflight_sequence: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_inflight(self) -> "ProducerStateEntry":
        if (
            self.inflight_sequence is not None
            and self.inflight_sequence >= self.next_sequence
        ):
            raise ValueError(
                "in-flight sequence must already be below next_sequence"
            )
        return self


class ProducerStateFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_id: Literal["visionrig/producer-state/v1"] = "visionrig/producer-state/v1"
    entries: dict[str, ProducerStateEntry] = Field(default_factory=dict)


def producer_state_key(
    *,
    gateway_url: str,
    source_id: str,
    source_type: str,
) -> str:
    canonical = json.dumps(
        {
            "gateway_url": gateway_url.rstrip("/"),
            "source_id": source_id,
            "source_type": source_type,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "producer-" + hashlib.sha256(canonical).hexdigest()


class ProducerStateStore:
    """Atomic local state file.

    One active producer process per producer key is required. Atomic replacement
    protects crash/restart integrity, not multi-process leader election.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()

    def _load(self) -> ProducerStateFile:
        if not self.path.exists():
            return ProducerStateFile()
        try:
            return ProducerStateFile.model_validate_json(
                self.path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise ProducerStateError(
                f"invalid producer state file: {self.path}"
            ) from exc

    def _save(self, state: ProducerStateFile) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
            raise ProducerStateError(
                f"unable to persist producer state: {self.path}"
            ) from exc
        finally:
            if temp_name is not None:
                try:
                    Path(temp_name).unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _entry(state: ProducerStateFile, key: str) -> ProducerStateEntry:
        return state.entries.get(key, ProducerStateEntry())

    @staticmethod
    def _with_entry(
        state: ProducerStateFile,
        key: str,
        entry: ProducerStateEntry,
    ) -> ProducerStateFile:
        entries = dict(state.entries)
        entries[key] = entry
        return state.model_copy(update={"entries": entries})

    def recover(self, key: str) -> ProducerStateEntry:
        if not key:
            raise ValueError("producer state key must not be empty")
        with self._lock:
            state = self._load()
            entry = self._entry(state, key)
            if entry.inflight_sequence is None:
                return entry

            recovered = ProducerStateEntry(
                next_sequence=entry.next_sequence,
                pending_dropped=entry.pending_dropped + 1,
                inflight_sequence=None,
            )
            self._save(self._with_entry(state, key, recovered))
            return recovered

    def reserve(self, key: str) -> tuple[int, int]:
        with self._lock:
            state = self._load()
            entry = self._entry(state, key)
            if entry.inflight_sequence is not None:
                raise ProducerStateError(
                    "producer state has an in-flight frame; recover before reserve"
                )
            sequence = entry.next_sequence
            reserved = ProducerStateEntry(
                next_sequence=sequence + 1,
                pending_dropped=entry.pending_dropped,
                inflight_sequence=sequence,
            )
            self._save(self._with_entry(state, key, reserved))
            return sequence, entry.pending_dropped

    def mark_dropped(self, key: str, sequence: int) -> ProducerStateEntry:
        with self._lock:
            state = self._load()
            entry = self._entry(state, key)
            if entry.inflight_sequence != sequence:
                raise ProducerStateError("drop does not match in-flight sequence")
            updated = ProducerStateEntry(
                next_sequence=entry.next_sequence,
                pending_dropped=entry.pending_dropped + 1,
                inflight_sequence=None,
            )
            self._save(self._with_entry(state, key, updated))
            return updated

    def mark_accepted(self, key: str, sequence: int) -> ProducerStateEntry:
        with self._lock:
            state = self._load()
            entry = self._entry(state, key)
            if entry.inflight_sequence != sequence:
                raise ProducerStateError("accept does not match in-flight sequence")
            updated = ProducerStateEntry(
                next_sequence=entry.next_sequence,
                pending_dropped=0,
                inflight_sequence=None,
            )
            self._save(self._with_entry(state, key, updated))
            return updated

    def snapshot(self, key: str) -> ProducerStateEntry:
        with self._lock:
            return self._entry(self._load(), key)
