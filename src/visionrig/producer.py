"""Reference client for VisionRig's authenticated sensor gateway."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

import httpx

from .sensor_ingress import SensorFrameReceipt


class ProducerError(RuntimeError):
    pass


class ProducerAuthError(ProducerError):
    pass


class ProducerProtocolError(ProducerError):
    pass


@dataclass(frozen=True, slots=True)
class ProducerFrameResult:
    status: Literal["accepted", "dropped_overload", "dropped_unavailable"]
    frame_sequence: int
    pending_dropped_frames: int
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProducerStats:
    captured: int
    accepted: int
    dropped_overload: int
    dropped_unavailable: int
    pending_dropped_frames: int
    next_sequence: int


def _gateway_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("gateway URL must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("gateway URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("gateway URL must not contain query or fragment")
    if parsed.hostname is None:
        raise ValueError("gateway URL has no hostname")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


class GatewayFrameProducer:
    """Stateful producer with explicit sequence/drop semantics.

    Every captured frame consumes a source sequence number. A 429/unavailable
    frame is counted as dropped and the count is attached to the next accepted
    frame. That keeps the downstream observation honest without retrying stale
    imagery.
    """

    def __init__(
        self,
        *,
        gateway_url: str,
        token: str,
        source_id: str,
        source_type: Literal["camera", "screen", "vr", "image"],
        device: str | None = None,
        start_sequence: int = 0,
        timeout_seconds: float = 5.0,
        client: httpx.Client | None = None,
    ) -> None:
        if len(token) < 32:
            raise ValueError("producer token must be at least 32 characters")
        if not source_id or len(source_id) > 128:
            raise ValueError("source_id must contain 1..128 characters")
        if device is not None and len(device) > 256:
            raise ValueError("device must be <= 256 characters")
        if start_sequence < 0:
            raise ValueError("start_sequence must be >= 0")
        if not 0.1 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 0.1 and 120")

        self._base_url = _gateway_base_url(gateway_url)
        self._token = token
        self._source_id = source_id
        self._source_type = source_type
        self._device = device
        self._next_sequence = start_sequence
        self._timeout = timeout_seconds
        self._client = client

        self._captured = 0
        self._accepted = 0
        self._dropped_overload = 0
        self._dropped_unavailable = 0
        self._pending_dropped = 0

    def stats(self) -> ProducerStats:
        return ProducerStats(
            captured=self._captured,
            accepted=self._accepted,
            dropped_overload=self._dropped_overload,
            dropped_unavailable=self._dropped_unavailable,
            pending_dropped_frames=self._pending_dropped,
            next_sequence=self._next_sequence,
        )

    def _post(self, *, sequence: int, payload: bytes, content_type: str) -> httpx.Response:
        params: dict[str, str | int] = {
            "source_id": self._source_id,
            "source_type": self._source_type,
            "frame_sequence": sequence,
            "dropped_frames": self._pending_dropped,
        }
        if self._device is not None:
            params["device"] = self._device

        kwargs = {
            "params": params,
            "content": payload,
            "headers": {
                "authorization": f"Bearer {self._token}",
                "content-type": content_type,
            },
            "timeout": self._timeout,
        }
        target = self._base_url + "/api/v1/frames/ingest"
        if self._client is not None:
            return self._client.post(target, **kwargs)
        with httpx.Client() as client:
            return client.post(target, **kwargs)

    def send_encoded(
        self,
        payload: bytes,
        *,
        content_type: Literal["image/jpeg", "image/png", "image/webp"] = "image/jpeg",
    ) -> ProducerFrameResult:
        if not payload:
            raise ValueError("frame payload must not be empty")

        sequence = self._next_sequence
        self._next_sequence += 1
        self._captured += 1

        try:
            response = self._post(
                sequence=sequence,
                payload=payload,
                content_type=content_type,
            )
        except httpx.HTTPError:
            self._pending_dropped += 1
            self._dropped_unavailable += 1
            return ProducerFrameResult(
                status="dropped_unavailable",
                frame_sequence=sequence,
                pending_dropped_frames=self._pending_dropped,
            )

        if response.status_code == 429:
            self._pending_dropped += 1
            self._dropped_overload += 1
            return ProducerFrameResult(
                status="dropped_overload",
                frame_sequence=sequence,
                pending_dropped_frames=self._pending_dropped,
            )

        if response.status_code in {401, 403}:
            self._pending_dropped += 1
            raise ProducerAuthError("VisionRig gateway rejected producer credentials")

        if response.status_code != 200:
            self._pending_dropped += 1
            raise ProducerProtocolError(
                f"VisionRig gateway returned HTTP {response.status_code}"
            )

        try:
            receipt = SensorFrameReceipt.model_validate(response.json())
        except Exception as exc:
            self._pending_dropped += 1
            raise ProducerProtocolError("invalid VisionRig sensor receipt") from exc

        if (
            receipt.source_id != self._source_id
            or receipt.source_type != self._source_type
            or receipt.frame_sequence != sequence
        ):
            self._pending_dropped += 1
            raise ProducerProtocolError("VisionRig receipt does not match submitted frame")

        expected_dropped = self._pending_dropped
        if receipt.dropped_frames != expected_dropped:
            self._pending_dropped += 1
            raise ProducerProtocolError(
                "VisionRig receipt dropped-frame count does not match producer state"
            )

        self._pending_dropped = 0
        self._accepted += 1
        return ProducerFrameResult(
            status="accepted",
            frame_sequence=sequence,
            pending_dropped_frames=0,
            event_id=receipt.event_id,
        )
