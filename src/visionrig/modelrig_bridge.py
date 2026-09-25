"""Best-effort VisionRig PerceptionEvent/v3 publisher for ModelRig.

The publisher is intentionally loopback-only. It sends no raw frames and applies
semantic change suppression before crossing the VisionRig -> ModelRig boundary.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import ipaddress
import json
from threading import Lock
import time
from typing import Callable, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .contracts import PerceptionEvent


class ModelRigBridgeConfigError(ValueError):
    pass


class ModelRigBridgeReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema: Literal["kaliv-consciousness-core/visionrig-admission/v1"]
    visionrig_event_ref: str = Field(min_length=1, max_length=256)
    evidence_ref: str = Field(min_length=1, max_length=256)
    cognition_event_id: str | None = None
    world_changed: bool
    replayed: bool
    cognition_event_queued: bool
    epistemic_status: Literal["inferred"]
    confidence: float = Field(ge=0.0, le=1.0)
    attention_salience: float = Field(ge=0.0, le=1.0)
    observed_sequence: int = Field(ge=0)
    model_calls: Literal[0]
    self_state_store_write_applied: Literal[False]
    durable_memory_write_authority: Literal[False]
    execution_authority: Literal[False]
    scheduling_authority: Literal[False]
    production_activation: Literal[False]


@dataclass(frozen=True, slots=True)
class BridgePublishResult:
    status: Literal["published", "replayed", "suppressed", "unavailable", "rejected"]
    source_id: str
    frame_sequence: int
    reason: str | None = None
    receipt: ModelRigBridgeReceipt | None = None


@dataclass(frozen=True, slots=True)
class BridgeStats:
    published: int
    replayed: int
    suppressed: int
    unavailable: int
    rejected: int


class SemanticChangeGate:
    """Suppress frame-to-frame noise while retaining meaningful scene changes."""

    def __init__(self) -> None:
        self._fingerprints: dict[str, str] = {}
        self._lock = Lock()

    @staticmethod
    def fingerprint(event: PerceptionEvent) -> str:
        kinds = Counter(item.kind for item in event.entities)
        labels = sorted(
            {
                " ".join(item.label.split())[:64]
                for item in event.entities
                if item.kind != "text"
            }
        )
        predicates = Counter(item.predicate for item in event.relations)
        metric = [
            float(item.distance_m)
            for item in event.depth
            if item.distance_m is not None
        ]
        # 25 cm buckets avoid publishing detector/depth jitter as cognition.
        nearest_bucket = (
            round(min(metric) * 4.0) / 4.0
            if metric
            else None
        )
        semantic = {
            "entity_kinds": sorted(kinds.items()),
            "labels": labels,
            "relations": sorted(predicates.items()),
            "nearest_metric_depth_bucket_m": nearest_bucket,
            "metric_depth_count": len(metric),
            "ocr_items": sum(1 for item in event.entities if item.kind == "text"),
            "landmark_groups": sorted(item.group for item in event.landmarks),
        }
        return hashlib.sha256(
            json.dumps(
                semantic,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()

    def changed(self, event: PerceptionEvent) -> tuple[bool, str]:
        fingerprint = self.fingerprint(event)
        with self._lock:
            return (
                self._fingerprints.get(event.source.source_id) != fingerprint,
                fingerprint,
            )

    def commit(self, event: PerceptionEvent, fingerprint: str) -> None:
        with self._lock:
            self._fingerprints[event.source.source_id] = fingerprint


class ModelRigPerceptionPublisher:
    """Synchronous, bounded, best-effort delivery to ModelRig worker loopback."""

    path = "/experimental/consciousness/visionrig-event"

    def __init__(
        self,
        worker_url: str = "http://127.0.0.1:8099",
        *,
        timeout_seconds: float = 2.0,
        retry_after_seconds: float = 5.0,
        gate: SemanticChangeGate | None = None,
        client: httpx.Client | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.worker_url = _normalize_loopback_url(worker_url)
        if not 0.1 <= timeout_seconds <= 30.0:
            raise ModelRigBridgeConfigError(
                "timeout_seconds must be between 0.1 and 30"
            )
        if not 0.0 <= retry_after_seconds <= 300.0:
            raise ModelRigBridgeConfigError(
                "retry_after_seconds must be between 0 and 300"
            )
        self._timeout = timeout_seconds
        self._retry_after = retry_after_seconds
        self._gate = gate or SemanticChangeGate()
        self._client = client or httpx.Client()
        self._owns_client = client is None
        self._monotonic = monotonic
        self._retry_not_before = 0.0
        self._lock = Lock()
        self._published = 0
        self._replayed = 0
        self._suppressed = 0
        self._unavailable = 0
        self._rejected = 0
        self._last_result: BridgePublishResult | None = None

    @property
    def endpoint(self) -> str:
        return self.worker_url + self.path

    @property
    def last_result(self) -> BridgePublishResult | None:
        with self._lock:
            return self._last_result

    def stats(self) -> BridgeStats:
        with self._lock:
            return BridgeStats(
                published=self._published,
                replayed=self._replayed,
                suppressed=self._suppressed,
                unavailable=self._unavailable,
                rejected=self._rejected,
            )

    def _record(self, result: BridgePublishResult) -> BridgePublishResult:
        with self._lock:
            if result.status == "published":
                self._published += 1
            elif result.status == "replayed":
                self._replayed += 1
            elif result.status == "suppressed":
                self._suppressed += 1
            elif result.status == "unavailable":
                self._unavailable += 1
            elif result.status == "rejected":
                self._rejected += 1
            self._last_result = result
        return result

    def publish(self, event: PerceptionEvent) -> BridgePublishResult:
        if not isinstance(event, PerceptionEvent):
            raise TypeError("event must be PerceptionEvent")

        changed, fingerprint = self._gate.changed(event)
        if not changed:
            return self._record(
                BridgePublishResult(
                    status="suppressed",
                    source_id=event.source.source_id,
                    frame_sequence=event.frame_sequence,
                    reason="semantic state unchanged",
                )
            )

        now = self._monotonic()
        with self._lock:
            retry_not_before = self._retry_not_before
        if now < retry_not_before:
            return self._record(
                BridgePublishResult(
                    status="unavailable",
                    source_id=event.source.source_id,
                    frame_sequence=event.frame_sequence,
                    reason="bridge retry backoff active",
                )
            )

        try:
            response = self._client.post(
                self.endpoint,
                json=event.model_dump(mode="json"),
                timeout=self._timeout,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            with self._lock:
                self._retry_not_before = now + self._retry_after
            return self._record(
                BridgePublishResult(
                    status="unavailable",
                    source_id=event.source.source_id,
                    frame_sequence=event.frame_sequence,
                    reason=type(exc).__name__,
                )
            )

        if response.status_code in {404, 503}:
            with self._lock:
                self._retry_not_before = now + self._retry_after
            return self._record(
                BridgePublishResult(
                    status="unavailable",
                    source_id=event.source.source_id,
                    frame_sequence=event.frame_sequence,
                    reason=f"ModelRig HTTP {response.status_code}",
                )
            )
        if response.status_code < 200 or response.status_code >= 300:
            return self._record(
                BridgePublishResult(
                    status="rejected",
                    source_id=event.source.source_id,
                    frame_sequence=event.frame_sequence,
                    reason=f"ModelRig HTTP {response.status_code}",
                )
            )

        try:
            receipt = ModelRigBridgeReceipt.model_validate(response.json())
        except Exception:
            return self._record(
                BridgePublishResult(
                    status="rejected",
                    source_id=event.source.source_id,
                    frame_sequence=event.frame_sequence,
                    reason="invalid ModelRig bridge receipt",
                )
            )

        expected_ref = _visionrig_event_ref(event)
        if (
            receipt.visionrig_event_ref != expected_ref
            or receipt.observed_sequence != event.frame_sequence
        ):
            return self._record(
                BridgePublishResult(
                    status="rejected",
                    source_id=event.source.source_id,
                    frame_sequence=event.frame_sequence,
                    reason="ModelRig bridge receipt binding mismatch",
                )
            )

        self._gate.commit(event, fingerprint)
        with self._lock:
            self._retry_not_before = 0.0
        status: Literal["published", "replayed"] = (
            "replayed" if receipt.replayed else "published"
        )
        return self._record(
            BridgePublishResult(
                status=status,
                source_id=event.source.source_id,
                frame_sequence=event.frame_sequence,
                receipt=receipt,
            )
        )

    def accept(self, event: PerceptionEvent) -> None:
        """PerceptionEventSink-compatible best-effort delivery."""
        self.publish(event)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def _visionrig_event_ref(event: PerceptionEvent) -> str:
    payload = json.dumps(
        event.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "visionrig-event:" + hashlib.sha256(payload).hexdigest()


def _normalize_loopback_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    try:
        parsed = urlparse(raw)
        host = parsed.hostname
    except ValueError as exc:
        raise ModelRigBridgeConfigError("invalid ModelRig worker URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ModelRigBridgeConfigError(
            "ModelRig worker URL must be a loopback HTTP(S) origin"
        )
    if host.lower() != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ModelRigBridgeConfigError(
                    "ModelRig worker URL must resolve explicitly to loopback"
                )
        except ValueError as exc:
            raise ModelRigBridgeConfigError(
                "ModelRig worker URL must use localhost or a loopback IP"
            ) from exc
    return raw
