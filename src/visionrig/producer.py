"""Reference client for VisionRig's authenticated sensor gateway."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

import httpx

from .producer_state import (
    ProducerStateStore,
    producer_state_key,
)
from .sensor_ingress import SensorFrameReceipt, SensorHeartbeatReceipt
from .sensor_registry import SensorDesiredState


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

    With a ProducerStateStore, frame reservation is persisted before network I/O.
    A crash with an in-flight frame is recovered as one dropped frame on restart.
    """

    def __init__(
        self,
        *,
        gateway_url: str,
        token: str,
        source_id: str,
        source_type: Literal["camera", "screen", "vr", "image"],
        device: str | None = None,
        start_sequence: int | None = None,
        timeout_seconds: float = 5.0,
        client: httpx.Client | None = None,
        state_store: ProducerStateStore | None = None,
    ) -> None:
        if len(token) < 32:
            raise ValueError("producer token must be at least 32 characters")
        if not source_id or len(source_id) > 128:
            raise ValueError("source_id must contain 1..128 characters")
        if device is not None and len(device) > 256:
            raise ValueError("device must be <= 256 characters")
        if start_sequence is not None and start_sequence < 0:
            raise ValueError("start_sequence must be >= 0")
        if not 0.1 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 0.1 and 120")
        if state_store is not None and start_sequence is not None:
            raise ValueError(
                "start_sequence cannot be combined with persistent producer state"
            )

        self._base_url = _gateway_base_url(gateway_url)
        self._token = token
        self._source_id = source_id
        self._source_type = source_type
        self._device = device
        self._timeout = timeout_seconds
        self._client = client
        self._state_store = state_store
        self._state_key = producer_state_key(
            gateway_url=self._base_url,
            source_id=source_id,
            source_type=source_type,
        )

        if state_store is not None:
            recovered = state_store.recover(self._state_key)
            self._next_sequence = recovered.next_sequence
            self._pending_dropped = recovered.pending_dropped
        else:
            self._next_sequence = start_sequence if start_sequence is not None else 0
            self._pending_dropped = 0

        self._captured = 0
        self._accepted = 0
        self._dropped_overload = 0
        self._dropped_unavailable = 0

    def _sync_persistent_state(self) -> None:
        if self._state_store is None:
            return
        state = self._state_store.snapshot(self._state_key)
        self._next_sequence = state.next_sequence
        self._pending_dropped = state.pending_dropped

    def stats(self) -> ProducerStats:
        self._sync_persistent_state()
        return ProducerStats(
            captured=self._captured,
            accepted=self._accepted,
            dropped_overload=self._dropped_overload,
            dropped_unavailable=self._dropped_unavailable,
            pending_dropped_frames=self._pending_dropped,
            next_sequence=self._next_sequence,
        )

    def send_heartbeat(
        self,
        *,
        capabilities: tuple[str, ...] = (),
        capture_active: bool | None = None,
        applied_revision: int | None = None,
    ) -> SensorHeartbeatReceipt:
        """Publish producer liveness without capturing or sending a frame."""
        target = self._base_url + "/api/v1/sensors/heartbeat"
        payload = {
            "source_id": self._source_id,
            "source_type": self._source_type,
            "device": self._device,
            "capabilities": list(capabilities),
            "capture_active": capture_active,
            "applied_revision": applied_revision,
        }
        kwargs = {
            "json": payload,
            "headers": {"authorization": f"Bearer {self._token}"},
            "timeout": self._timeout,
        }
        try:
            if self._client is not None:
                response = self._client.post(target, **kwargs)
            else:
                with httpx.Client() as client:
                    response = client.post(target, **kwargs)
        except httpx.HTTPError as exc:
            raise ProducerError("VisionRig gateway heartbeat unavailable") from exc

        if response.status_code in {401, 403}:
            raise ProducerAuthError("VisionRig gateway rejected producer credentials")
        if response.status_code != 200:
            raise ProducerProtocolError(
                f"VisionRig heartbeat endpoint returned HTTP {response.status_code}"
            )
        try:
            receipt = SensorHeartbeatReceipt.model_validate(response.json())
        except Exception as exc:
            raise ProducerProtocolError("invalid VisionRig heartbeat receipt") from exc
        if receipt.source_id != self._source_id:
            raise ProducerProtocolError(
                "VisionRig heartbeat receipt does not match producer source"
            )
        return receipt

    def fetch_desired_state(self) -> SensorDesiredState:
        """Fetch the operator desired state through the authenticated gateway."""
        target = self._base_url + f"/api/v1/sensors/{self._source_id}/desired-state"
        kwargs = {
            "headers": {"authorization": f"Bearer {self._token}"},
            "timeout": self._timeout,
        }
        try:
            if self._client is not None:
                response = self._client.get(target, **kwargs)
            else:
                with httpx.Client() as client:
                    response = client.get(target, **kwargs)
        except httpx.HTTPError as exc:
            raise ProducerError("VisionRig gateway desired state unavailable") from exc

        if response.status_code in {401, 403}:
            raise ProducerAuthError("VisionRig gateway rejected producer credentials")
        if response.status_code != 200:
            raise ProducerProtocolError(
                f"VisionRig desired-state endpoint returned HTTP {response.status_code}"
            )
        try:
            state = SensorDesiredState.model_validate(response.json())
        except Exception as exc:
            raise ProducerProtocolError("invalid VisionRig sensor desired state") from exc
        if state.source_id != self._source_id:
            raise ProducerProtocolError(
                "VisionRig desired state does not match producer source"
            )
        return state

    def _reserve_frame(self) -> tuple[int, int]:
        if self._state_store is None:
            sequence = self._next_sequence
            self._next_sequence += 1
            return sequence, self._pending_dropped

        sequence, pending = self._state_store.reserve(self._state_key)
        self._sync_persistent_state()
        return sequence, pending

    def _mark_dropped(self, sequence: int) -> None:
        if self._state_store is None:
            self._pending_dropped += 1
            return
        self._state_store.mark_dropped(self._state_key, sequence)
        self._sync_persistent_state()

    def _mark_accepted(self, sequence: int) -> None:
        if self._state_store is None:
            self._pending_dropped = 0
            return
        self._state_store.mark_accepted(self._state_key, sequence)
        self._sync_persistent_state()

    def _post(
        self,
        *,
        sequence: int,
        pending_dropped: int,
        payload: bytes,
        content_type: str,
    ) -> httpx.Response:
        params: dict[str, str | int] = {
            "source_id": self._source_id,
            "source_type": self._source_type,
            "frame_sequence": sequence,
            "dropped_frames": pending_dropped,
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

        sequence, pending_before_send = self._reserve_frame()
        self._captured += 1

        try:
            response = self._post(
                sequence=sequence,
                pending_dropped=pending_before_send,
                payload=payload,
                content_type=content_type,
            )
        except httpx.HTTPError:
            self._mark_dropped(sequence)
            self._dropped_unavailable += 1
            return ProducerFrameResult(
                status="dropped_unavailable",
                frame_sequence=sequence,
                pending_dropped_frames=self._pending_dropped,
            )

        if response.status_code == 429:
            self._mark_dropped(sequence)
            self._dropped_overload += 1
            return ProducerFrameResult(
                status="dropped_overload",
                frame_sequence=sequence,
                pending_dropped_frames=self._pending_dropped,
            )

        if response.status_code in {401, 403}:
            self._mark_dropped(sequence)
            raise ProducerAuthError("VisionRig gateway rejected producer credentials")

        if response.status_code != 200:
            self._mark_dropped(sequence)
            raise ProducerProtocolError(
                f"VisionRig gateway returned HTTP {response.status_code}"
            )

        try:
            receipt = SensorFrameReceipt.model_validate(response.json())
        except Exception as exc:
            self._mark_dropped(sequence)
            raise ProducerProtocolError("invalid VisionRig sensor receipt") from exc

        if (
            receipt.source_id != self._source_id
            or receipt.source_type != self._source_type
            or receipt.frame_sequence != sequence
        ):
            self._mark_dropped(sequence)
            raise ProducerProtocolError("VisionRig receipt does not match submitted frame")

        if receipt.dropped_frames != pending_before_send:
            self._mark_dropped(sequence)
            raise ProducerProtocolError(
                "VisionRig receipt dropped-frame count does not match producer state"
            )

        self._mark_accepted(sequence)
        self._accepted += 1
        return ProducerFrameResult(
            status="accepted",
            frame_sequence=sequence,
            pending_dropped_frames=0,
            event_id=receipt.event_id,
        )
