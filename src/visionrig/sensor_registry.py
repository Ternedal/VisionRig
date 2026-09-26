"""Operator-owned metadata for known VisionRig sensor sources.

This registry is deliberately separate from perception truth. It gives control
surfaces stable labels, placement/role hints and an operator enable preference
without allowing a sensor producer to grant itself authority.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from threading import RLock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SensorRole = Literal["ambient", "primary", "tracking", "screen", "vr", "other"]


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


class SensorRegistry:
    """Thread-safe in-process registry for operator metadata."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._entries: dict[str, SensorMetadata] = {}

    @staticmethod
    def _clean(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

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
            self._entries[source_id] = updated
            return updated
