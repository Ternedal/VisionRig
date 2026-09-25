"""Bounded encoded-frame ingress for Kaliv/Windows/VR producers.

Remote producers submit one encoded image at a time. VisionRig admits at most one
raw frame to the inference pipeline concurrently; overload is rejected instead
of building unbounded visual latency.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .contracts import PerceptionEvent, SourceDescriptor
from .pipeline import Frame
from .runtime import VisionRuntime


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
    source_type: Literal["camera", "screen", "vr", "image"]
    frame_sequence: int = Field(ge=0)
    event_id: str = Field(min_length=1, max_length=128)
    dropped_frames: int = Field(ge=0)
    production_authority: Literal[False] = False


@dataclass(frozen=True, slots=True)
class SensorSourceStats:
    source_id: str
    source_type: str
    device: str | None
    last_sequence: int
    accepted_frames: int
    dropped_frames_total: int
    last_seen_utc: str


@dataclass(frozen=True, slots=True)
class SensorIngressStats:
    schema: str
    accepted_total: int
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
    last_sequence: int
    accepted_frames: int
    dropped_frames_total: int
    last_seen_utc: str


class SensorIngress:
    """Single-slot inference admission with per-source sequence protection."""

    def __init__(
        self,
        runtime: VisionRuntime,
        *,
        decoder: ImageDecoder | None = None,
        max_payload_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        if max_payload_bytes < 1024:
            raise ValueError("max_payload_bytes must be >= 1024")
        self._runtime = runtime
        self._decoder = decoder or OpenCVImageDecoder()
        self._max_payload_bytes = max_payload_bytes
        self._processing = Lock()
        self._metrics_lock = Lock()
        self._last_sequence: dict[str, int] = {}
        self._sources: dict[str, _MutableSourceStats] = {}
        self._accepted_total = 0
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

    def _record_accept(
        self,
        *,
        source_id: str,
        source_type: str,
        device: str | None,
        frame_sequence: int,
        dropped_frames: int,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._metrics_lock:
            self._accepted_total += 1
            current = self._sources.get(source_id)
            if current is None:
                self._sources[source_id] = _MutableSourceStats(
                    source_type=source_type,
                    device=device,
                    last_sequence=frame_sequence,
                    accepted_frames=1,
                    dropped_frames_total=dropped_frames,
                    last_seen_utc=now,
                )
                return
            current.source_type = source_type
            current.device = device
            current.last_sequence = frame_sequence
            current.accepted_frames += 1
            current.dropped_frames_total += dropped_frames
            current.last_seen_utc = now

    def stats(self) -> SensorIngressStats:
        with self._metrics_lock:
            sources = tuple(
                SensorSourceStats(
                    source_id=source_id,
                    source_type=state.source_type,
                    device=state.device,
                    last_sequence=state.last_sequence,
                    accepted_frames=state.accepted_frames,
                    dropped_frames_total=state.dropped_frames_total,
                    last_seen_utc=state.last_seen_utc,
                )
                for source_id, state in sorted(self._sources.items())
            )
            return SensorIngressStats(
                schema="visionrig/sensor-runtime-status/v1",
                accepted_total=self._accepted_total,
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
        source_type: Literal["camera", "screen", "vr", "image"],
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
