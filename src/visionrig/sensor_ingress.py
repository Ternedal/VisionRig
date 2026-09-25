"""Bounded encoded-frame ingress for Kaliv/Windows/VR producers.

Remote producers submit one encoded image at a time. VisionRig admits at most one
raw frame to the inference pipeline concurrently; overload is rejected instead
of building unbounded visual latency.
"""
from __future__ import annotations

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
        self._last_sequence: dict[str, int] = {}

    @property
    def max_payload_bytes(self) -> int:
        return self._max_payload_bytes

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
            raise SensorDecodeError("sensor frame payload is empty")
        if len(payload) > self._max_payload_bytes:
            raise SensorPayloadTooLarge(
                f"sensor frame exceeds {self._max_payload_bytes} byte limit"
            )
        if frame_sequence < 0:
            raise SensorSequenceError("frame_sequence must be >= 0")
        if dropped_frames < 0:
            raise SensorSequenceError("dropped_frames must be >= 0")

        normalized_content_type = content_type.split(";", 1)[0].strip().lower()
        if normalized_content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise SensorMediaTypeError(
                "encoded sensor frames must be image/jpeg, image/png or image/webp"
            )

        if not self._processing.acquire(blocking=False):
            raise SensorIngressBusy("VisionRig sensor ingress is busy")

        try:
            previous = self._last_sequence.get(source_id)
            if previous is not None and frame_sequence <= previous:
                raise SensorSequenceError(
                    "frame_sequence must increase monotonically per source"
                )

            image = self._decoder.decode(payload, normalized_content_type)
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
