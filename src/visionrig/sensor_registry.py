"""Operator-owned metadata for known VisionRig sensor sources.

The registry is deliberately separate from perception truth. It gives control
surfaces stable labels, placement/role hints and an operator enable preference
without allowing a sensor producer to grant itself authority.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
from threading import RLock
import tempfile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


SensorRole = Literal["ambient", "primary", "tracking", "screen", "vr", "other"]


class SensorRegistryError(RuntimeError):
    pass


class SensorMetadataPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=128)
    location: str | None = Field(default=None, max_length=128)
    role: SensorRole | None = None
    enabled: bool | None = None


@dataclass(frozen=True, slots=True)
class SensorMetadata:
    source_id: str
    display_name: str | None = None
    location: str | None = None
    role: SensorRole | None = None
    enabled: bool = True


class _StoredSensorMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)
    location: str | None = Field(default=None, max_length=128)
    role: SensorRole | None = None
    enabled: bool = True


class _SensorRegistryFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["visionrig/sensor-registry-file/v1"] = (
        "visionrig/sensor-registry-file/v1"
    )
    entries: tuple[_StoredSensorMetadata, ...] = ()


class SensorRegistry:
    """Thread-safe registry with optional crash-safe JSON persistence."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = RLock()
        self.path = Path(path).expanduser() if path is not None else None
        self._entries: dict[str, SensorMetadata] = {}
        if self.path is not None:
            self._load()

    @property
    def persistent(self) -> bool:
        return self.path is not None

    @staticmethod
    def _clean(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    def _load(self) -> None:
        assert self.path is not None
        if not self.path.exists():
            return
        try:
            state = _SensorRegistryFile.model_validate_json(
                self.path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise SensorRegistryError(
                f"invalid sensor registry file: {self.path}"
            ) from exc
        self._entries = {
            entry.source_id: SensorMetadata(
                source_id=entry.source_id,
                display_name=entry.display_name,
                location=entry.location,
                role=entry.role,
                enabled=entry.enabled,
            )
            for entry in state.entries
        }

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        state = _SensorRegistryFile(
            entries=tuple(
                _StoredSensorMetadata(
                    source_id=entry.source_id,
                    display_name=entry.display_name,
                    location=entry.location,
                    role=entry.role,
                    enabled=entry.enabled,
                )
                for entry in self.list()
            )
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
            raise SensorRegistryError(
                f"unable to persist sensor registry: {self.path}"
            ) from exc
        finally:
            if temp_name is not None:
                try:
                    Path(temp_name).unlink(missing_ok=True)
                except OSError:
                    pass

    def get(self, source_id: str) -> SensorMetadata:
        with self._lock:
            return self._entries.get(source_id, SensorMetadata(source_id=source_id))

    def list(self) -> tuple[SensorMetadata, ...]:
        with self._lock:
            return tuple(self._entries[key] for key in sorted(self._entries))

    def patch(self, source_id: str, patch: SensorMetadataPatch) -> SensorMetadata:
        if not source_id or len(source_id) > 128:
            raise ValueError("source_id must contain 1..128 characters")
        with self._lock:
            current = self._entries.get(source_id, SensorMetadata(source_id=source_id))
            changes = {}
            if "display_name" in patch.model_fields_set:
                changes["display_name"] = self._clean(patch.display_name)
            if "location" in patch.model_fields_set:
                changes["location"] = self._clean(patch.location)
            if "role" in patch.model_fields_set:
                changes["role"] = patch.role
            if "enabled" in patch.model_fields_set:
                if patch.enabled is None:
                    raise ValueError("enabled cannot be null")
                changes["enabled"] = patch.enabled
            updated = replace(current, **changes)
            previous = self._entries.get(source_id)
            self._entries[source_id] = updated
            try:
                self._save()
            except Exception:
                if previous is None:
                    self._entries.pop(source_id, None)
                else:
                    self._entries[source_id] = previous
                raise
            return updated
