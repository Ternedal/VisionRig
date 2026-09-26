import httpx
import pytest

from visionrig.producer import (
    GatewayFrameProducer,
    ProducerAuthError,
    ProducerProtocolError,
)


TOKEN = "p" * 48


def _receipt(sequence: int, dropped: int = 0):
    return {
        "schema_id": "visionrig/sensor-frame-receipt/v1",
        "status": "processed",
        "source_id": "quest",
        "source_type": "vr",
        "frame_sequence": sequence,
        "event_id": f"evt-{sequence}",
        "dropped_frames": dropped,
        "production_authority": False,
    }


def test_producer_consumes_sequences_and_reports_overload_drop() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        sequence = int(request.url.params["frame_sequence"])
        if sequence == 1:
            return httpx.Response(429, json={"detail": "busy"})
        dropped = int(request.url.params["dropped_frames"])
        return httpx.Response(200, json=_receipt(sequence, dropped))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        producer = GatewayFrameProducer(
            gateway_url="http://100.64.0.2:8111",
            token=TOKEN,
            source_id="quest",
            source_type="vr",
            client=client,
        )
        first = producer.send_encoded(b"a")
        second = producer.send_encoded(b"b")
        third = producer.send_encoded(b"c")

    assert first.status == "accepted"
    assert second.status == "dropped_overload"
    assert third.status == "accepted"
    assert [int(r.url.params["frame_sequence"]) for r in requests] == [0, 1, 2]
    assert [int(r.url.params["dropped_frames"]) for r in requests] == [0, 0, 1]
    stats = producer.stats()
    assert stats.captured == 3
    assert stats.accepted == 2
    assert stats.dropped_overload == 1
    assert stats.pending_dropped_frames == 0


def test_network_failure_is_counted_and_propagated_to_next_success() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("offline", request=request)
        dropped = int(request.url.params["dropped_frames"])
        sequence = int(request.url.params["frame_sequence"])
        return httpx.Response(200, json=_receipt(sequence, dropped))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        producer = GatewayFrameProducer(
            gateway_url="http://100.64.0.2:8111",
            token=TOKEN,
            source_id="quest",
            source_type="vr",
            client=client,
        )
        lost = producer.send_encoded(b"a")
        accepted = producer.send_encoded(b"b")

    assert lost.status == "dropped_unavailable"
    assert accepted.status == "accepted"
    assert producer.stats().dropped_unavailable == 1
    assert producer.stats().pending_dropped_frames == 0


def test_auth_failure_stops_fail_closed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "bad token"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        producer = GatewayFrameProducer(
            gateway_url="http://127.0.0.1:8111",
            token=TOKEN,
            source_id="quest",
            source_type="vr",
            client=client,
        )
        with pytest.raises(ProducerAuthError):
            producer.send_encoded(b"a")


def test_mismatched_receipt_fails_closed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        body = _receipt(999)
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        producer = GatewayFrameProducer(
            gateway_url="http://127.0.0.1:8111",
            token=TOKEN,
            source_id="quest",
            source_type="vr",
            client=client,
        )
        with pytest.raises(ProducerProtocolError, match="does not match"):
            producer.send_encoded(b"a")


def test_producer_sends_heartbeat_without_consuming_sequence() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["authorization"] = request.headers.get("authorization")
        seen["json"] = __import__("json").loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "schema_id": "visionrig/sensor-heartbeat-receipt/v1",
                "status": "accepted",
                "source_id": "quest",
                "seen_utc": "2026-09-26T03:00:00+00:00",
                "production_authority": False,
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        producer = GatewayFrameProducer(
            gateway_url="http://100.64.0.2:8111",
            token=TOKEN,
            source_id="quest",
            source_type="vr",
            device="quest-2",
            client=client,
        )
        receipt = producer.send_heartbeat(
            capabilities=("rgb", "passthrough"),
            capture_active=False,
            applied_revision=9,
        )
        stats = producer.stats()

    assert receipt.source_id == "quest"
    assert seen["path"] == "/api/v1/sensors/heartbeat"
    assert seen["authorization"] == f"Bearer {TOKEN}"
    assert seen["json"]["device"] == "quest-2"
    assert seen["json"]["capabilities"] == ["rgb", "passthrough"]
    assert seen["json"]["capture_active"] is False
    assert seen["json"]["applied_revision"] == 9
    assert stats.next_sequence == 0
    assert stats.captured == 0
