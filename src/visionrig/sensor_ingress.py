"""Bounded encoded-frame ingress for Kaliv/Windows/VR producers.

Remote producers submit one encoded image at a time. VisionRig admits at most one
raw frame to the inference pipeline concurrently; overload is rejected instead
of building unbounded visual latency. Producers can also publish lightweight
heartbeats so operator surfaces can distinguish online, stale and offline
sensors without exposing raw image data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .contracts import PerceptionEvent, SourceDescriptor
from .pipeline import Frame
from .runtime import VisionRuntime


SensorSourceType = Literal["camera", "screen", "vr", "image"]
SensorPresence = Literal["online", "stale", "offline"]


class SensorIngressError(RuntimeError):
    pass


class SensorIngressBusy(SensorIngressError):
    pass


class SensorSequenceError(SensorIngressError):
    pass


class SensorPayloadTooLarge(SensorIngressError):
    pass


class SensorMediaTypeError(SensorIngressError):
    pass


class SensorDecodeError(SensorIngressError):
    pass


class ImageDecoder(Protocol):
    def decode(self, payload: bytes, content_type: str) -> Any: ...


class OpenCVImageDecoder:
    """Lazy OpenCV decoder so core/service import remains capture-optional."""

    def decode(self, payload: bytes, content_type: str) -> Any:
        if content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise SensorMediaTypeError(
                "encoded sensor frames must be image/jpeg, image/png or image/webp"
            )
        try:
            import cv2  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]
        except ImportError as exc:
            raise SensorDecodeError(
                'encoded frame ingress requires VisionRig ".[capture]"'
            ) from exc

        encoded = np.frombuffer(payload, dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if image is None:
            raise SensorDecodeError("unable to decode encoded sensor frame")
        return image


class SensorFrameReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["visionrig/sensor-frame-receipt/v1"] = (
        "visionrig/sensor-frame-receipt/v1"
    )
    status: Literal["processed"] = "processed"
    source_id: str = Field(min_length=1, max_length=128)
    source_type: SensorSourceType
    frame_sequence: int = Field(ge=0)
    event_id: str = Field(min_length=1, max_length=128)
    dropped_frames: int = Field(ge=0)
    production_authority: Literal[False] = False


class SensorHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["visionrig/sensor-heartbeat/v2"] = (
        "visionrig/sensor-heartbeat/v2"
    )
    source_id: str = Field(min_length=1, max_length=128)
    source_type: SensorSourceType
    device: str | None = Field(default=None, max_length=256)
    capabilities: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    capture_active: bool | None = None
    applied_revision: int | None = Field(default=None, ge=0)


class SensorHeartbeatReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["visionrig/sensor-heartbeat-receipt/v1"] = (
        "visionrig/sensor-heartbeat-receipt/v1"
    )
    status: Literal["accepted"] = "accepted"
    source_id: str = Field(min_length=1, max_length=128)
    seen_utc: str
    production_authority: Literal[False] = False


@dataclass(frozen=True, slots=True)
class SensorSourceStats:
    source_id: str
    source_type: str
    device: str | None
    capabilities: tuple[str, ...]
    capture_active: bool | None
    applied_revision: int | None
    presence: SensorPresence
    age_seconds: float
    last_sequence: int | None
    accepted_frames: int
    heartbeat_count: int
    dropped_frames_total: int
    last_seen_utc: str


@dataclass(frozen=True, slots=True)
class SensorIngressStats:
    schema: str
    stale_after_seconds: float
    offline_after_seconds: float
    accepted_total: int
    heartbeat_total: int
    rejected_busy_total: int
    rejected_sequence_total: int
    rejected_payload_total: int
    rejected_media_type_total: int
    rejected_decode_total: int
    active_processing: bool
    sources: tuple[SensorSourceStats, ...]


@dataclass(slots=True)
class _MutableSourceStats:
    source_type: str
    device: str | None
    capabilities: tuple[str, ...]
    capture_active: bool | None
    applied_revision: int | None
    last_sequence: int | None
    accepted_frames: int
    heartbeat_count: int
    dropped_frames_total: int
    last_seen: datetime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SensorIngress:
    """Single-slot inference admission with per-source sequence protection."""

    def __init__(
        self,
        runtime: VisionRuntime,
        *,
        decoder: ImageDecoder | None = None,
        max_payload_bytes: int = 8 * 1024 * 1024,
        stale_after_seconds: float = 15.0,
        offline_after_seconds: float = 60.0,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        if max_payload_bytes < 1024:
            raise ValueError("max_payload_bytes must be >= 1024")
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be > 0")
        if offline_after_seconds <= stale_after_seconds:
            raise ValueError("offline_after_seconds must exceed stale_after_seconds")
        self._runtime = runtime
        self._decoder = decoder or OpenCVImageDecoder()
        self._max_payload_bytes = max_payload_bytes
        self._stale_after_seconds = float(stale_after_seconds)
        self._offline_after_seconds = float(offline_after_seconds)
        self._clock = clock
        self._processing = Lock()
        self._metrics_lock = Lock()
        self._last_sequence: dict[str, int] = {}
        self._sources: dict[str, _MutableSourceStats] = {}
        self._accepted_total = 0
        self._heartbeat_total = 0
        self._rejected_busy_total = 0
        self._rejected_sequence_total = 0
        self._rejected_payload_total = 0
        self._rejected_media_type_total = 0
        self._rejected_decode_total = 0

    @property
    def max_payload_bytes(self) -> int:
        return self._max_payload_bytes

    def _increment_rejection(self, kind: str) -> None:
        with self._metrics_lock:
            attribute = f"_rejected_{kind}_total"
            setattr(self, attribute, getattr(self, attribute) + 1)

    @staticmethod
    def _normalize_capabilities(values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = []
        for value in values:
            item = value.strip().lower()
            if not item or len(item) > 64:
                raise ValueError("sensor capabilities must contain 1..64 characters")
            if item not in cleaned:
                cleaned.append(item)
        return tuple(sorted(cleaned))

    def forget_source(self, source_id: str) -> None:
        """Drop process-local runtime and sequence state for a forgotten source."""
        with self._metrics_lock:
            self._sources.pop(source_id, None)
            self._last_sequence.pop(source_id, None)

    def heartbeat(self, heartbeat: SensorHeartbeat) -> SensorHeartbeatReceipt:
        now = self._clock()
        capabilities = self._normalize_capabilities(heartbeat.capabilities)
        with self._metrics_lock:
            self._heartbeat_total += 1
            current = self._sources.get(heartbeat.source_id)
            if current is None:
                self._sources[heartbeat.source_id] = _MutableSourceStats(
                    source_type=heartbeat.source_type,
                    device=heartbeat.device,
                    capabilities=capabilities,
                    capture_active=heartbeat.capture_active,
                    applied_revision=heartbeat.applied_revision,
                    last_sequence=None,
                    accepted_frames=0,
                    heartbeat_count=1,
                    dropped_frames_total=0,
                    last_seen=now,
                )
            else:
                current.source_type = heartbeat.source_type
                current.device = heartbeat.device
                current.capabilities = capabilities
                current.capture_active = heartbeat.capture_active
                current.applied_revision = heartbeat.applied_revision
                current.heartbeat_count += 1
                current.last_seen = now
        return SensorHeartbeatReceipt(
            source_id=heartbeat.source_id,
            seen_utc=now.isoformat(),
            production_authority=False,
        )

    def _record_accept(
        self,
        *,
        source_id: str,
        source_type: str,
        device: str | None,
        frame_sequence: int,
        dropped_frames: int,
    ) -> None:
        now = self._clock()
        with self._metrics_lock:
            self._accepted_total += 1
            current = self._sources.get(source_id)
            if current is None:
                self._sources[source_id] = _MutableSourceStats(
                    source_type=source_type,
                    device=device,
                    capabilities=(),
                    capture_active=True,
                    applied_revision=None,
                    last_sequence=frame_sequence,
                    accepted_frames=1,
                    heartbeat_count=0,
                    dropped_frames_total=dropped_frames,
                    last_seen=now,
                )
                return
            current.source_type = source_type
            current.device = device
            current.capture_active = True
            current.last_sequence = frame_sequence
            current.accepted_frames += 1
            current.dropped_frames_total += dropped_frames
            current.last_seen = now

    def _presence(self, age_seconds: float) -> SensorPresence:
        if age_seconds <= self._stale_after_seconds:
            return "online"
        if age_seconds <= self._offline_after_seconds:
            return "stale"
        return "offline"

    def stats(self) -> SensorIngressStats:
        now = self._clock()
        with self._metrics_lock:
            sources = tuple(
                SensorSourceStats(
                    source_id=source_id,
                    source_type=state.source_type,
                    device=state.device,
                    capabilities=state.capabilities,
                    capture_active=state.capture_active,
                    applied_revision=state.applied_revision,
                    presence=self._presence(
                        max(0.0, (now - state.last_seen).total_seconds())
                    ),
                    age_seconds=round(
                        max(0.0, (now - state.last_seen).total_seconds()),
                        3,
                    ),
                    last_sequence=state.last_sequence,
                    accepted_frames=state.accepted_frames,
                    heartbeat_count=state.heartbeat_count,
                    dropped_frames_total=state.dropped_frames_total,
                    last_seen_utc=state.last_seen.isoformat(),
                )
                for source_id, state in sorted(self._sources.items())
            )
            return SensorIngressStats(
                schema="visionrig/sensor-runtime-status/v4",
                stale_after_seconds=self._stale_after_seconds,
                offline_after_seconds=self._offline_after_seconds,
                accepted_total=self._accepted_total,
                heartbeat_total=self._heartbeat_total,
                rejected_busy_total=self._rejected_busy_total,
                rejected_sequence_total=self._rejected_sequence_total,
                rejected_payload_total=self._rejected_payload_total,
                rejected_media_type_total=self._rejected_media_type_total,
                rejected_decode_total=self._rejected_decode_total,
                active_processing=self._processing.locked(),
                sources=sources,
            )

    def process_encoded(
        self,
        *,
        source_id: str,
        source_type: SensorSourceType,
        frame_sequence: int,
        payload: bytes,
        content_type: str,
        device: str | None = None,
        dropped_frames: int = 0,
    ) -> SensorFrameReceipt:
        if not payload:
            self._increment_rejection("decode")
            raise SensorDecodeError("sensor frame payload is empty")
        if len(payload) > self._max_payload_bytes:
            self._increment_rejection("payload")
            raise SensorPayloadTooLarge(
                f"sensor frame exceeds {self._max_payload_bytes} byte limit"
            )
        if frame_sequence < 0:
            self._increment_rejection("sequence")
            raise SensorSequenceError("frame_sequence must be >= 0")
        if dropped_frames < 0:
            self._increment_rejection("sequence")
            raise SensorSequenceError("dropped_frames must be >= 0")

        normalized_content_type = content_type.split(";", 1)[0].strip().lower()
        if normalized_content_type not in {"image/jpeg", "image/png", "image/webp"}:
            self._increment_rejection("media_type")
            raise SensorMediaTypeError(
                "encoded sensor frames must be image/jpeg, image/png or image/webp"
            )

        if not self._processing.acquire(blocking=False):
            self._increment_rejection("busy")
            raise SensorIngressBusy("VisionRig sensor ingress is busy")

        try:
            previous = self._last_sequence.get(source_id)
            if previous is not None and frame_sequence <= previous:
                self._increment_rejection("sequence")
                raise SensorSequenceError(
                    "frame_sequence must increase monotonically per source"
                )

            try:
                image = self._decoder.decode(payload, normalized_content_type)
            except SensorDecodeError:
                self._increment_rejection("decode")
                raise

            event = self._runtime.process_direct(
                Frame(
                    source=SourceDescriptor(
                        source_id=source_id,
                        source_type=source_type,
                        device=device,
                    ),
                    sequence=frame_sequence,
                    payload=image,
                    dropped_frames=dropped_frames,
                )
            )
            self._last_sequence[source_id] = frame_sequence
            self._record_accept(
                source_id=source_id,
                source_type=source_type,
                device=device,
                frame_sequence=event.frame_sequence,
                dropped_frames=event.dropped_frames,
            )
            return SensorFrameReceipt(
                source_id=source_id,
                source_type=source_type,
                frame_sequence=event.frame_sequence,
                event_id=event.event_id,
                dropped_frames=event.dropped_frames,
                production_authority=False,
            )
        finally:
            self._processing.release()
