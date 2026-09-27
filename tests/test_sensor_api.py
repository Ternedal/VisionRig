from datetime import datetime, timezone

from fastapi.testclient import TestClient

import visionrig.sensor_ingress as sensor_ingress
from visionrig.api import create_app
from visionrig.pipeline import PerceptionPipeline


class FakeCVDecoder:
    def decode(self, payload: bytes, content_type: str):
        return {"bytes": len(payload), "content_type": content_type}


def test_raw_sensor_ingest_returns_receipt_and_event(monkeypatch) -> None:
    monkeypatch.setattr(sensor_ingress, "OpenCVImageDecoder", lambda: FakeCVDecoder())
    client = TestClient(create_app(PerceptionPipeline(), max_sensor_frame_bytes=1024))

    response = client.post(
        "/api/v1/frames/ingest",
        params={"source_id": "kaliv-screen", "source_type": "screen", "frame_sequence": 1},
        content=b"encoded-frame",
        headers={"content-type": "image/jpeg"},
    )
    assert response.status_code == 200
    assert response.json()["schema_id"] == "visionrig/sensor-frame-receipt/v1"

    events = client.get("/api/v1/perception/events").json()
    assert len(events["entries"]) == 1
    assert events["entries"][0]["event"]["source"]["source_type"] == "screen"


def test_sensor_status_tracks_sources_drops_and_health(monkeypatch) -> None:
    monkeypatch.setattr(sensor_ingress, "OpenCVImageDecoder", lambda: FakeCVDecoder())
    client = TestClient(create_app(PerceptionPipeline(), max_sensor_frame_bytes=1024))

    for sequence, dropped in ((4, 2), (5, 1)):
        response = client.post(
            "/api/v1/frames/ingest",
            params={
                "source_id": "kaliv-vr",
                "source_type": "vr",
                "frame_sequence": sequence,
                "device": "quest",
                "dropped_frames": dropped,
            },
            content=b"encoded-frame",
            headers={"content-type": "image/jpeg"},
        )
        assert response.status_code == 200

    body = client.get("/api/v1/sensors/status").json()
    assert body["schema"] == "visionrig/sensor-runtime-status/v12"
    assert body["accepted_total"] == 2
    assert body["active_processing"] is False
    source = body["sources"][0]
    assert source["source_id"] == "kaliv-vr"
    assert source["source_type"] == "vr"
    assert source["device"] == "quest"
    assert source["presence"] == "online"
    assert source["last_sequence"] == 5
    assert source["accepted_frames"] == 2
    assert source["dropped_frames_total"] == 3
    assert source["packet_transport"] is None
    assert source["last_seen_utc"].endswith("+00:00")

    health = client.get("/health").json()
    assert health["schema"] == "visionrig/health/v35"
    assert health["sensor_ingress"]["schema"] == "visionrig/sensor-ingress/v9"
    assert health["sensor_ingress"]["heartbeat_schemas"] == [
        "visionrig/sensor-heartbeat/v2",
        "visionrig/sensor-heartbeat/v3",
        "visionrig/sensor-heartbeat/v4",
        "visionrig/sensor-heartbeat/v5",
        "visionrig/sensor-heartbeat/v6",
    ]
    assert health["sensor_ingress"]["sensor_packet_schemas"] == [
        "visionrig/sensor-packet/v1",
        "visionrig/sensor-packet/v2",
    ]
    assert health["sensor_ingress"]["sensor_packet_compressions"] == [
        "none",
        "zlib",
    ]
    assert health["sensor_ingress"]["sensor_packet_transport_schema"] == (
        "visionrig/sensor-packet-transport/v2"
    )
    assert health["sensor_ingress"]["sensor_packet_payload_thresholds"] == {
        "warning": 0.8,
        "critical": 0.95,
    }
    assert health["sensor_ingress"]["runtime"]["accepted_total"] == 2


def test_sensor_heartbeat_registers_capabilities_without_frame() -> None:
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_stale_after_seconds=10,
            sensor_offline_after_seconds=30,
        )
    )
    response = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v2",
            "source_id": "kinect-living-room",
            "source_type": "camera",
            "device": "kinect-v2",
            "capabilities": ["RGB", "depth", "infrared", "depth"],
            "applied_revision": 2,
        },
    )
    assert response.status_code == 200
    assert response.json()["schema_id"] == "visionrig/sensor-heartbeat-receipt/v1"

    body = client.get("/api/v1/sensors/status").json()
    assert body["heartbeat_total"] == 1
    source = body["sources"][0]
    assert source["source_id"] == "kinect-living-room"
    assert source["last_sequence"] is None
    assert source["accepted_frames"] == 0
    assert source["heartbeat_count"] == 1
    assert source["capabilities"] == ["depth", "infrared", "rgb"]
    assert source["applied_revision"] == 2
    assert source["negotiated_max_payload_bytes"] is None
    assert source["negotiated_packet_compression"] is None
    assert source["capability_refreshed_utc"] is None
    assert source["capability_refresh_observed_utc"] is None
    assert source["capability_refresh_age_seconds"] is None
    assert source["capability_refresh_status"] == "unknown"
    assert source["presence"] == "online"


def test_sensor_status_counts_sequence_rejections(monkeypatch) -> None:
    monkeypatch.setattr(sensor_ingress, "OpenCVImageDecoder", lambda: FakeCVDecoder())
    client = TestClient(create_app(PerceptionPipeline(), max_sensor_frame_bytes=1024))

    params = {"source_id": "camera-a", "source_type": "camera", "frame_sequence": 7}
    first = client.post(
        "/api/v1/frames/ingest",
        params=params,
        content=b"encoded-frame",
        headers={"content-type": "image/jpeg"},
    )
    assert first.status_code == 200

    duplicate = client.post(
        "/api/v1/frames/ingest",
        params=params,
        content=b"encoded-frame",
        headers={"content-type": "image/jpeg"},
    )
    assert duplicate.status_code == 409

    body = client.get("/api/v1/sensors/status").json()
    assert body["accepted_total"] == 1
    assert body["rejected_sequence_total"] == 1


def test_raw_sensor_ingest_rejects_large_declared_body(monkeypatch) -> None:
    monkeypatch.setattr(sensor_ingress, "OpenCVImageDecoder", lambda: FakeCVDecoder())
    client = TestClient(create_app(PerceptionPipeline(), max_sensor_frame_bytes=1024))
    response = client.post(
        "/api/v1/frames/ingest",
        params={"source_id": "cam", "source_type": "camera", "frame_sequence": 1},
        content=b"x" * 1025,
        headers={"content-type": "image/jpeg"},
    )
    assert response.status_code == 413


def test_raw_sensor_ingest_rejects_wrong_media_type(monkeypatch) -> None:
    monkeypatch.setattr(sensor_ingress, "OpenCVImageDecoder", lambda: FakeCVDecoder())
    client = TestClient(create_app(PerceptionPipeline(), max_sensor_frame_bytes=1024))
    response = client.post(
        "/api/v1/frames/ingest",
        params={"source_id": "cam", "source_type": "camera", "frame_sequence": 1},
        content=b"x",
        headers={"content-type": "application/octet-stream"},
    )
    assert response.status_code == 415
    assert client.get("/api/v1/sensors/status").json()["rejected_media_type_total"] == 1


def test_frame_ingest_auto_registers_source_without_heartbeat(monkeypatch) -> None:
    monkeypatch.setattr(sensor_ingress, "OpenCVImageDecoder", lambda: FakeCVDecoder())
    from visionrig.sensor_registry import SensorRegistry

    registry = SensorRegistry()
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            max_sensor_frame_bytes=1024,
            sensor_registry=registry,
        )
    )

    response = client.post(
        "/api/v1/frames/ingest",
        params={
            "source_id": "screen-new",
            "source_type": "screen",
            "frame_sequence": 0,
        },
        content=b"encoded-frame",
        headers={"content-type": "image/jpeg"},
    )
    assert response.status_code == 200
    assert registry.get("screen-new").source_id == "screen-new"
    assert [entry.source_id for entry in registry.list()] == ["screen-new"]


def test_sensor_heartbeat_v3_exposes_negotiated_transport_runtime_state() -> None:
    client = TestClient(create_app(PerceptionPipeline()))
    refreshed = datetime.now(timezone.utc).isoformat()

    response = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v3",
            "source_id": "kinect-negotiated",
            "source_type": "camera",
            "device": "kinect-v2",
            "capabilities": ["rgb", "depth", "infrared"],
            "capture_active": True,
            "applied_revision": 3,
            "negotiated_max_payload_bytes": 4 * 1024 * 1024,
            "capability_refreshed_utc": refreshed,
            "capability_refresh_seconds": 30.0,
        },
    )
    assert response.status_code == 200

    source = client.get("/api/v1/sensors/status").json()["sources"][0]
    assert source["negotiated_max_payload_bytes"] == 4 * 1024 * 1024
    assert source["capability_refreshed_utc"] == refreshed
    assert source["capability_refresh_observed_utc"] is not None
    assert source["capability_refresh_age_seconds"] >= 0
    assert source["capability_refresh_status"] == "current"


def test_sensor_heartbeat_v3_ignores_producer_clock_skew_for_freshness() -> None:
    now = [datetime(2026, 9, 27, 7, 0, tzinfo=timezone.utc)]
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_clock=lambda: now[0],
        )
    )

    response = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v3",
            "source_id": "kinect-clock-skew",
            "source_type": "camera",
            "negotiated_max_payload_bytes": 4 * 1024 * 1024,
            "capability_refreshed_utc": "2020-01-01T00:00:00+00:00",
            "capability_refresh_seconds": 30.0,
        },
    )
    assert response.status_code == 200

    source = client.get("/api/v1/sensors/status").json()["sources"][0]
    assert source["capability_refreshed_utc"] == "2020-01-01T00:00:00+00:00"
    assert source["capability_refresh_observed_utc"] == "2026-09-27T07:00:00+00:00"
    assert source["capability_refresh_status"] == "current"
    assert source["capability_refresh_age_seconds"] == 0.0

    now[0] = datetime(2026, 9, 27, 7, 1, 1, tzinfo=timezone.utc)
    stale = client.get("/api/v1/sensors/status").json()["sources"][0]
    assert stale["capability_refresh_status"] == "stale"
    assert stale["capability_refresh_age_seconds"] == 61.0


def test_sensor_heartbeat_v3_rejects_partial_negotiation_telemetry() -> None:
    client = TestClient(create_app(PerceptionPipeline()))

    response = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v3",
            "source_id": "broken-negotiation",
            "source_type": "camera",
            "negotiated_max_payload_bytes": 4 * 1024 * 1024,
        },
    )

    assert response.status_code == 422


def test_sensor_heartbeat_v4_exposes_negotiated_compression() -> None:
    client = TestClient(create_app(PerceptionPipeline()))
    refreshed = datetime.now(timezone.utc).isoformat()
    response = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v4",
            "source_id": "kinect-v4",
            "source_type": "camera",
            "negotiated_max_payload_bytes": 4194304,
            "capability_refreshed_utc": refreshed,
            "capability_refresh_seconds": 30.0,
            "negotiated_packet_compression": "auto",
        },
    )
    assert response.status_code == 200
    source = client.get("/api/v1/sensors/status").json()["sources"][0]
    assert source["negotiated_max_payload_bytes"] == 4194304
    assert source["negotiated_packet_compression"] == "auto"
    assert source["capability_refresh_status"] == "current"


def test_sensor_heartbeat_v3_rejects_compression_field() -> None:
    client = TestClient(create_app(PerceptionPipeline()))
    response = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v3",
            "source_id": "bad-v3",
            "source_type": "camera",
            "negotiated_max_payload_bytes": 4194304,
            "capability_refreshed_utc": "2026-09-27T08:00:00+00:00",
            "capability_refresh_seconds": 30.0,
            "negotiated_packet_compression": "zlib",
        },
    )
    assert response.status_code == 422


def test_sensor_heartbeat_v5_exposes_target_utilization() -> None:
    client = TestClient(create_app(PerceptionPipeline()))
    response = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v5",
            "source_id": "kinect-v5",
            "source_type": "camera",
            "negotiated_max_payload_bytes": 4194304,
            "capability_refreshed_utc": "2026-09-27T10:00:00+00:00",
            "capability_refresh_seconds": 30.0,
            "negotiated_packet_compression": "auto",
            "negotiated_packet_target_utilization": 0.72,
        },
    )
    assert response.status_code == 200
    source = client.get("/api/v1/sensors/status").json()["sources"][0]
    assert source["negotiated_packet_compression"] == "auto"
    assert source["negotiated_packet_target_utilization"] == 0.72


def test_sensor_heartbeat_v4_rejects_target_utilization() -> None:
    client = TestClient(create_app(PerceptionPipeline()))
    response = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v4",
            "source_id": "bad-v4",
            "source_type": "camera",
            "negotiated_max_payload_bytes": 4194304,
            "capability_refreshed_utc": "2026-09-27T10:00:00+00:00",
            "capability_refresh_seconds": 30.0,
            "negotiated_packet_compression": "auto",
            "negotiated_packet_target_utilization": 0.72,
        },
    )
    assert response.status_code == 422


def test_sensor_heartbeat_v6_exposes_observed_packet_utilization() -> None:
    client = TestClient(create_app(PerceptionPipeline()))
    response = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v6",
            "source_id": "kinect-v6",
            "source_type": "camera",
            "negotiated_max_payload_bytes": 4194304,
            "capability_refreshed_utc": "2026-09-27T11:00:00+00:00",
            "capability_refresh_seconds": 30.0,
            "negotiated_packet_compression": "auto",
            "negotiated_packet_target_utilization": 0.72,
            "observed_packet_utilization": 0.691,
        },
    )
    assert response.status_code == 200
    source = client.get("/api/v1/sensors/status").json()["sources"][0]
    assert source["observed_packet_utilization"] == 0.691


def test_sensor_heartbeat_v5_rejects_observed_packet_utilization() -> None:
    client = TestClient(create_app(PerceptionPipeline()))
    response = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v5",
            "source_id": "bad-v5",
            "source_type": "camera",
            "negotiated_max_payload_bytes": 4194304,
            "capability_refreshed_utc": "2026-09-27T11:00:00+00:00",
            "capability_refresh_seconds": 30.0,
            "negotiated_packet_compression": "auto",
            "negotiated_packet_target_utilization": 0.72,
            "observed_packet_utilization": 0.691,
        },
    )
    assert response.status_code == 422


def test_fleet_marks_packet_target_compliance_and_attention() -> None:
    client = TestClient(create_app(PerceptionPipeline()))
    response = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v6",
            "source_id": "kinect-over-target",
            "source_type": "camera",
            "capture_active": True,
            "applied_revision": 0,
            "negotiated_max_payload_bytes": 4194304,
            "capability_refreshed_utc": "2026-09-27T12:00:00+00:00",
            "capability_refresh_seconds": 30.0,
            "negotiated_packet_compression": "auto",
            "negotiated_packet_target_utilization": 0.72,
            "observed_packet_utilization": 0.76,
        },
    )
    assert response.status_code == 200

    fleet = client.get("/api/v1/sensors/fleet").json()
    assert fleet["schema"] == "visionrig/sensor-fleet-summary/v8"
    assert fleet["packet_target"] == {
        "within_target": 0,
        "above_target": 1,
        "unknown": 0,
    }
    assert fleet["attention_total"] == 1
    item = fleet["attention"][0]
    assert item["source_id"] == "kinect-over-target"
    assert item["packet_target_status"] == "above_target"
    assert item["negotiated_packet_target_utilization"] == 0.72
    assert item["observed_packet_utilization"] == 0.76
    assert "packet_target" in item["reasons"]


def test_fleet_packet_target_within_target_does_not_raise_attention() -> None:
    client = TestClient(create_app(PerceptionPipeline()))
    response = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v6",
            "source_id": "kinect-within-target",
            "source_type": "camera",
            "capture_active": True,
            "applied_revision": 0,
            "negotiated_max_payload_bytes": 4194304,
            "capability_refreshed_utc": "2026-09-27T12:00:00+00:00",
            "capability_refresh_seconds": 30.0,
            "negotiated_packet_compression": "auto",
            "negotiated_packet_target_utilization": 0.72,
            "observed_packet_utilization": 0.70,
        },
    )
    assert response.status_code == 200

    fleet = client.get("/api/v1/sensors/fleet").json()
    assert fleet["packet_target"] == {
        "within_target": 1,
        "above_target": 0,
        "unknown": 0,
    }
    assert fleet["attention_total"] == 0
