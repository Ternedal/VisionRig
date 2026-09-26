"""Operator-owned metadata for known VisionRig sensor sources.

The registry is deliberately separate from perception truth. It gives control
surfaces stable labels, placement/role hints and an operator enable preference
without allowing a sensor producer to grant itself authority.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import os
from pathlib import Path
from threading import RLock
import tempfile
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


SensorRole = Literal["ambient", "primary", "tracking", "screen", "vr", "other"]


class SensorRegistryError(RuntimeError):
    pass


class SensorIdentityConflict(SensorRegistryError):
    pass


class SensorMetadataPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=128)
    location: str | None = Field(default=None, max_length=128)
    role: SensorRole | None = None
    enabled: bool | None = None


class SensorDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["visionrig/sensor-desired-state/v2"] = (
        "visionrig/sensor-desired-state/v2"
    )
    source_id: str = Field(min_length=1, max_length=128)
    enabled: bool
    revision: int = Field(ge=0)
    production_authority: Literal[False] = False


@dataclass(frozen=True, slots=True)
class SensorDiscovery:
    source_id: str
    source_type: str
    device: str | None = None
    capabilities: tuple[str, ...] = ()
    first_seen_utc: str | None = None
    last_seen_utc: str | None = None
    observation_count: int = 0


@dataclass(frozen=True, slots=True)
class SensorMetadata:
    source_id: str
    display_name: str | None = None
    location: str | None = None
    role: SensorRole | None = None
    enabled: bool = True
    retired_utc: str | None = None


class _StoredSensorMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)
    location: str | None = Field(default=None, max_length=128)
    role: SensorRole | None = None
    enabled: bool = True
    retired_utc: str | None = None


class _StoredSensorDiscovery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1, max_length=128)
    source_type: str = Field(min_length=1, max_length=32)
    device: str | None = Field(default=None, max_length=256)
    capabilities: tuple[str, ...] = ()
    first_seen_utc: str | None = None
    last_seen_utc: str | None = None
    observation_count: int = Field(default=0, ge=0)


@dataclass(frozen=True, slots=True)
class SensorControlState:
    source_id: str
    revision: int = 0
    changed_utc: str | None = None


class _StoredSensorControl(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(default=0, ge=0)
    changed_utc: str | None = None


class _SensorRegistryFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["visionrig/sensor-registry-file/v1"] = (
        "visionrig/sensor-registry-file/v1"
    )
    entries: tuple[_StoredSensorMetadata, ...] = ()
    discovery: tuple[_StoredSensorDiscovery, ...] = ()
    control: tuple[_StoredSensorControl, ...] = ()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SensorRegistry:
    """Thread-safe registry with optional crash-safe JSON persistence."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._lock = RLock()
        self.path = Path(path).expanduser() if path is not None else None
        self._clock = clock
        self._entries: dict[str, SensorMetadata] = {}
        self._discovery: dict[str, SensorDiscovery] = {}
        self._control_revisions: dict[str, int] = {}
        self._control_changed_utc: dict[str, str] = {}
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
                retired_utc=entry.retired_utc,
            )
            for entry in state.entries
        }
        self._discovery = {
            item.source_id: SensorDiscovery(
                source_id=item.source_id,
                source_type=item.source_type,
                device=item.device,
                capabilities=tuple(item.capabilities),
                first_seen_utc=item.first_seen_utc,
                last_seen_utc=item.last_seen_utc,
                observation_count=item.observation_count,
            )
            for item in state.discovery
        }
        self._control_revisions = {
            item.source_id: item.revision
            for item in state.control
        }
        self._control_changed_utc = {
            item.source_id: item.changed_utc
            for item in state.control
            if item.changed_utc is not None
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
                    retired_utc=entry.retired_utc,
                )
                for entry in self.list()
            ),
            discovery=tuple(
                _StoredSensorDiscovery(
                    source_id=item.source_id,
                    source_type=item.source_type,
                    device=item.device,
                    capabilities=item.capabilities,
                    first_seen_utc=item.first_seen_utc,
                    last_seen_utc=item.last_seen_utc,
                    observation_count=item.observation_count,
                )
                for item in self.list_discovery()
            ),
            control=tuple(
                _StoredSensorControl(
                    source_id=source_id,
                    revision=revision,
                    changed_utc=self._control_changed_utc.get(source_id),
                )
                for source_id, revision in sorted(self._control_revisions.items())
                if revision > 0
            ),
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

    def contains(self, source_id: str) -> bool:
        with self._lock:
            return source_id in self._entries or source_id in self._discovery

    def get(self, source_id: str) -> SensorMetadata:
        with self._lock:
            return self._entries.get(source_id, SensorMetadata(source_id=source_id))

    def ensure_registered(self, source_id: str) -> SensorMetadata:
        """Persist a first-seen source without changing operator-owned metadata."""
        if not source_id or len(source_id) > 128:
            raise ValueError("source_id must contain 1..128 characters")
        with self._lock:
            current = self._entries.get(source_id)
            if current is not None:
                return current
            created = SensorMetadata(source_id=source_id)
            self._entries[source_id] = created
            try:
                self._save()
            except Exception:
                self._entries.pop(source_id, None)
                raise
            return created

    def get_discovery(self, source_id: str) -> SensorDiscovery | None:
        with self._lock:
            return self._discovery.get(source_id)

    def list_discovery(self) -> tuple[SensorDiscovery, ...]:
        with self._lock:
            return tuple(self._discovery[key] for key in sorted(self._discovery))

    def observe(
        self,
        source_id: str,
        *,
        source_type: str,
        device: str | None = None,
        capabilities: tuple[str, ...] = (),
    ) -> SensorDiscovery:
        """Persist producer-described discovery data without operator authority."""
        if not source_id or len(source_id) > 128:
            raise ValueError("source_id must contain 1..128 characters")
        cleaned_type = source_type.strip().lower()
        if not cleaned_type or len(cleaned_type) > 32:
            raise ValueError("source_type must contain 1..32 characters")
        cleaned_caps = tuple(sorted({
            value.strip().lower()
            for value in capabilities
            if value.strip()
        }))
        now = self._clock().astimezone(timezone.utc).isoformat()
        with self._lock:
            current = self._discovery.get(source_id)
            if current is not None and current.source_type != cleaned_type:
                raise SensorIdentityConflict(
                    f"source_id {source_id!r} is already registered as "
                    f"{current.source_type!r}, not {cleaned_type!r}"
                )
            updated = SensorDiscovery(
                source_id=source_id,
                source_type=cleaned_type,
                device=(
                    device
                    if device is not None
                    else (current.device if current is not None else None)
                ),
                capabilities=cleaned_caps or (
                    current.capabilities if current is not None else ()
                ),
                first_seen_utc=(
                    current.first_seen_utc
                    if current is not None and current.first_seen_utc is not None
                    else now
                ),
                last_seen_utc=now,
                observation_count=(
                    current.observation_count + 1 if current is not None else 1
                ),
            )
            previous_discovery = current
            previous_metadata = self._entries.get(source_id)
            if previous_metadata is None:
                self._entries[source_id] = SensorMetadata(source_id=source_id)
            self._discovery[source_id] = updated
            try:
                self._save()
            except Exception:
                if previous_metadata is None:
                    self._entries.pop(source_id, None)
                if previous_discovery is None:
                    self._discovery.pop(source_id, None)
                else:
                    self._discovery[source_id] = previous_discovery
                raise
            return updated

    def control_revision(self, source_id: str) -> int:
        with self._lock:
            return self._control_revisions.get(source_id, 0)

    def control_state(self, source_id: str) -> SensorControlState:
        with self._lock:
            return SensorControlState(
                source_id=source_id,
                revision=self._control_revisions.get(source_id, 0),
                changed_utc=self._control_changed_utc.get(source_id),
            )

    def control_pending_seconds(self, source_id: str) -> float | None:
        state = self.control_state(source_id)
        if state.changed_utc is None:
            return None
        try:
            changed = datetime.fromisoformat(state.changed_utc)
        except ValueError:
            return None
        if changed.tzinfo is None:
            changed = changed.replace(tzinfo=timezone.utc)
        now = self._clock().astimezone(timezone.utc)
        return round(max(0.0, (now - changed.astimezone(timezone.utc)).total_seconds()), 3)

    def retire(self, source_id: str) -> SensorMetadata:
        """Retire a sensor while preserving metadata, discovery and history."""
        if not source_id or len(source_id) > 128:
            raise ValueError("source_id must contain 1..128 characters")
        with self._lock:
            current = self._entries.get(source_id, SensorMetadata(source_id=source_id))
            if current.retired_utc is not None:
                return current
            now = self._clock().astimezone(timezone.utc).isoformat()
            updated = replace(current, enabled=False, retired_utc=now)
            previous = self._entries.get(source_id)
            previous_revision = self._control_revisions.get(source_id, 0)
            previous_changed_utc = self._control_changed_utc.get(source_id)
            self._entries[source_id] = updated
            if current.enabled:
                self._control_revisions[source_id] = previous_revision + 1
                self._control_changed_utc[source_id] = now
            try:
                self._save()
            except Exception:
                if previous is None:
                    self._entries.pop(source_id, None)
                else:
                    self._entries[source_id] = previous
                if previous_revision == 0:
                    self._control_revisions.pop(source_id, None)
                else:
                    self._control_revisions[source_id] = previous_revision
                if previous_changed_utc is None:
                    self._control_changed_utc.pop(source_id, None)
                else:
                    self._control_changed_utc[source_id] = previous_changed_utc
                raise
            return updated

    def restore(self, source_id: str) -> SensorMetadata:
        """Restore a retired sensor to managed-but-disabled state."""
        if not source_id or len(source_id) > 128:
            raise ValueError("source_id must contain 1..128 characters")
        with self._lock:
            current = self._entries.get(source_id, SensorMetadata(source_id=source_id))
            if current.retired_utc is None:
                return current
            updated = replace(current, retired_utc=None, enabled=False)
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

    def forget(self, source_id: str) -> None:
        """Permanently remove retired sensor metadata, discovery and control history."""
        if not source_id or len(source_id) > 128:
            raise ValueError("source_id must contain 1..128 characters")
        with self._lock:
            if not self.contains(source_id):
                raise KeyError(source_id)
            current = self._entries.get(source_id)
            if current is None or current.retired_utc is None:
                raise ValueError("sensor must be retired before it can be forgotten")

            previous_metadata = current
            previous_discovery = self._discovery.get(source_id)
            previous_revision = self._control_revisions.get(source_id)
            previous_changed_utc = self._control_changed_utc.get(source_id)

            self._entries.pop(source_id, None)
            self._discovery.pop(source_id, None)
            self._control_revisions.pop(source_id, None)
            self._control_changed_utc.pop(source_id, None)
            try:
                self._save()
            except Exception:
                self._entries[source_id] = previous_metadata
                if previous_discovery is not None:
                    self._discovery[source_id] = previous_discovery
                if previous_revision is not None:
                    self._control_revisions[source_id] = previous_revision
                if previous_changed_utc is not None:
                    self._control_changed_utc[source_id] = previous_changed_utc
                raise

    def desired_state(self, source_id: str) -> SensorDesiredState:
        metadata = self.get(source_id)
        return SensorDesiredState(
            source_id=source_id,
            enabled=metadata.enabled,
            revision=self.control_revision(source_id),
            production_authority=False,
        )

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
                if patch.enabled and current.retired_utc is not None:
                    raise ValueError("retired sensor must be restored before enabling")
                changes["enabled"] = patch.enabled
            updated = replace(current, **changes)
            previous = self._entries.get(source_id)
            previous_revision = self._control_revisions.get(source_id, 0)
            previous_changed_utc = self._control_changed_utc.get(source_id)
            self._entries[source_id] = updated
            if (
                "enabled" in patch.model_fields_set
                and updated.enabled != current.enabled
            ):
                self._control_revisions[source_id] = previous_revision + 1
                self._control_changed_utc[source_id] = (
                    self._clock().astimezone(timezone.utc).isoformat()
                )
            try:
                self._save()
            except Exception:
                if previous is None:
                    self._entries.pop(source_id, None)
                else:
                    self._entries[source_id] = previous
                if previous_revision == 0:
                    self._control_revisions.pop(source_id, None)
                else:
                    self._control_revisions[source_id] = previous_revision
                if previous_changed_utc is None:
                    self._control_changed_utc.pop(source_id, None)
                else:
                    self._control_changed_utc[source_id] = previous_changed_utc
                raise
            return updated
