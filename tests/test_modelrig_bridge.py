import hashlib
import json

import httpx
import pytest

from visionrig.contracts import (
    BoundingBox,
    DepthObservation,
    PerceptionEvent,
    SourceDescriptor,
    VisualEntity,
)
from visionrig.modelrig_bridge import (
    ModelRigBridgeConfigError,
    ModelRigPerceptionPublisher,
)
from visionrig.pipeline import Frame, PerceptionPipeline
from visionrig.runtime import VisionRuntime


def event(
    *,
    event_id: str = "evt-one",
    sequence: int = 1,
    distance_m: float = 1.25,
    confidence: float = 0.95,
    scene_label: str | None = None,
) -> PerceptionEvent:
    source = SourceDescriptor(
        source_id="kinect-v2-0",
        source_type="camera",
        device="kinect-v2",
    )
    return PerceptionEvent.now(
        event_id=event_id,
        source=source,
        frame_sequence=sequence,
        entities=(
            VisualEntity(
                entity_id=f"person-{sequence}",
                kind="person",
                label="person",
                confidence=confidence,
                bbox=BoundingBox(
                    x=0.1,
                    y=0.1,
                    width=0.3,
                    height=0.7,
                ),
            ),
        ),
        scene_label=scene_label,
        scene_confidence=(0.9 if scene_label is not None else None),
        depth=(
            DepthObservation(
                subject_entity_id=f"person-{sequence}",
                relative_depth=0.2,
                distance_m=distance_m,
                confidence=1.0,
                method="kinect-v2-hardware-depth",
            ),
        ),
    )


def receipt_for_request(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    canonical = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    event_ref = "visionrig-event:" + hashlib.sha256(canonical).hexdigest()
    return httpx.Response(
        200,
        json={
            "schema": "kaliv-consciousness-core/visionrig-admission/v1",
            "visionrig_event_ref": event_ref,
            "evidence_ref": "world-evidence-event:" + "a" * 64,
            "cognition_event_id": "cevt-" + "b" * 32,
            "world_changed": True,
            "replayed": False,
            "cognition_event_queued": True,
            "epistemic_status": "inferred",
            "confidence": 0.9,
            "attention_salience": 0.7,
            "observed_sequence": body["frame_sequence"],
            "model_calls": 0,
            "self_state_store_write_applied": False,
            "durable_memory_write_authority": False,
            "execution_authority": False,
            "scheduling_authority": False,
            "production_activation": False,
        },
    )


def test_bridge_requires_loopback_worker_url() -> None:
    with pytest.raises(ModelRigBridgeConfigError):
        ModelRigPerceptionPublisher("http://192.168.1.10:8099")
    with pytest.raises(ModelRigBridgeConfigError):
        ModelRigPerceptionPublisher("https://example.com")
    with pytest.raises(ModelRigBridgeConfigError):
        ModelRigPerceptionPublisher("http://user:pass@127.0.0.1:8099")

    publisher = ModelRigPerceptionPublisher(
        "http://localhost:8099",
        client=httpx.Client(transport=httpx.MockTransport(receipt_for_request)),
    )
    assert publisher.endpoint.endswith("/experimental/consciousness/visionrig-event")


def test_semantically_unchanged_frames_are_suppressed() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return receipt_for_request(request)

    publisher = ModelRigPerceptionPublisher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    first = publisher.publish(event())
    second = publisher.publish(
        event(
            event_id="evt-two",
            sequence=2,
            confidence=0.72,
        )
    )

    assert first.status == "published"
    assert second.status == "suppressed"
    assert calls == 1
    stats = publisher.stats()
    assert stats.published == 1
    assert stats.suppressed == 1


def test_meaningful_metric_depth_bucket_change_is_published() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return receipt_for_request(request)

    publisher = ModelRigPerceptionPublisher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert publisher.publish(event(distance_m=1.25)).status == "published"
    assert (
        publisher.publish(
            event(
                event_id="evt-depth-change",
                sequence=2,
                distance_m=1.80,
            )
        ).status
        == "published"
    )
    assert calls == 2


def test_unavailable_bridge_uses_backoff_without_committing_semantics() -> None:
    calls = 0
    clock = iter([10.0, 11.0, 16.0])

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("offline", request=request)

    publisher = ModelRigPerceptionPublisher(
        retry_after_seconds=5.0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        monotonic=lambda: next(clock),
    )

    first = publisher.publish(event())
    second = publisher.publish(event(event_id="evt-retry-early", sequence=2))
    third = publisher.publish(event(event_id="evt-retry-late", sequence=3))

    assert first.status == "unavailable"
    assert second.status == "unavailable"
    assert second.reason == "bridge retry backoff active"
    assert third.status == "unavailable"
    assert calls == 2


def test_receipt_binding_mismatch_is_rejected_and_not_committed() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        response = receipt_for_request(request)
        body = response.json()
        body["visionrig_event_ref"] = "visionrig-event:" + "f" * 64
        return httpx.Response(200, json=body)

    publisher = ModelRigPerceptionPublisher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    first = publisher.publish(event())
    second = publisher.publish(event(event_id="evt-second", sequence=2))

    assert first.status == "rejected"
    assert second.status == "rejected"
    assert calls == 2


class _FailingSink:
    def __init__(self) -> None:
        self.calls = 0

    def accept(self, event: PerceptionEvent) -> None:
        self.calls += 1
        raise RuntimeError("downstream unavailable")


def test_runtime_isolates_optional_sink_failure() -> None:
    sink = _FailingSink()
    runtime = VisionRuntime(
        PerceptionPipeline(),
        event_sinks=(sink,),
    )
    frame = Frame(
        source=SourceDescriptor(source_id="cam", source_type="camera"),
        sequence=1,
        payload=None,
    )

    accepted = runtime.process_direct(frame)

    assert accepted.frame_sequence == 1
    assert runtime.snapshot().last_event_id == accepted.event_id
    stats = runtime.sink_stats()
    assert stats.configured == 1
    assert stats.dispatched_total == 1
    assert stats.failed_total == 1
    assert sink.calls == 1


def test_scene_change_is_semantically_published() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return receipt_for_request(request)

    publisher = ModelRigPerceptionPublisher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert publisher.publish(event(scene_label="mrvision-place:office")).status == "published"
    assert (
        publisher.publish(
            event(
                event_id="evt-room-change",
                sequence=2,
                scene_label="mrvision-place:kitchen",
            )
        ).status
        == "published"
    )
    assert calls == 2
