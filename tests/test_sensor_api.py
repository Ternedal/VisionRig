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
    assert body["schema"] == "visionrig/sensor-runtime-status/v15"
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
    assert health["schema"] == "visionrig/health/v42"
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


def test_fleet_marks_sustained_packet_target_exceedance_as_attention() -> None:
    client = TestClient(create_app(PerceptionPipeline()))
    heartbeat = {
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
    }

    for expected_streak in (1, 2):
        response = client.post("/api/v1/sensors/heartbeat", json=heartbeat)
        assert response.status_code == 200
        runtime = client.get("/api/v1/sensors/status").json()["sources"][0]
        assert runtime["packet_target_above_streak"] == expected_streak
        fleet = client.get("/api/v1/sensors/fleet").json()
        assert fleet["packet_target_pressure"] == {
            "clear": 0,
            "transient": 1,
            "sustained": 0,
            "unknown": 0,
        }
        assert fleet["attention_total"] == 0

    response = client.post("/api/v1/sensors/heartbeat", json=heartbeat)
    assert response.status_code == 200

    fleet = client.get("/api/v1/sensors/fleet").json()
    assert fleet["schema"] == "visionrig/sensor-fleet-summary/v13"
    assert fleet["packet_target_attention_streak_threshold"] == 3
    assert fleet["packet_target"] == {
        "within_target": 0,
        "above_target": 1,
        "unknown": 0,
    }
    assert fleet["packet_target_pressure"] == {
        "clear": 0,
        "transient": 0,
        "sustained": 1,
        "unknown": 0,
    }
    assert fleet["packet_target_overshoot"] == {
        "measured_sources": 1,
        "max_delta": 0.04,
        "max_ratio": 0.055556,
    }
    assert fleet["attention_total"] == 1
    item = fleet["attention"][0]
    assert item["source_id"] == "kinect-over-target"
    assert item["packet_target_status"] == "above_target"
    assert item["packet_target_pressure_status"] == "sustained"
    assert item["packet_target_overshoot_delta"] == 0.04
    assert item["packet_target_overshoot_ratio"] == 0.055556
    assert item["packet_target_above_streak"] == 3
    assert item["packet_target_above_since_utc"] is not None
    assert item["packet_target_above_seconds"] is not None
    assert item["packet_target_last_above_utc"] is not None
    assert item["packet_target_sustained_episode_count"] == 1
    assert item["packet_target_last_recovered_utc"] is None
    assert item["packet_target_recurrence_count"] == 0
    assert item["packet_target_last_recurrence_seconds"] is None
    assert fleet["packet_target_sustained_episode_total"] == 1
    assert fleet["packet_target_recovered_sources"] == 0
    assert fleet["packet_target_recurrence_total"] == 0
    assert fleet["packet_target_recurring_sources"] == 0
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
    assert fleet["packet_target_pressure"] == {
        "clear": 1,
        "transient": 0,
        "sustained": 0,
        "unknown": 0,
    }
    assert fleet["attention_total"] == 0


def test_packet_target_streak_resets_after_compliant_measurement() -> None:
    client = TestClient(create_app(PerceptionPipeline()))
    heartbeat = {
        "schema_id": "visionrig/sensor-heartbeat/v6",
        "source_id": "kinect-streak-reset",
        "source_type": "camera",
        "negotiated_max_payload_bytes": 4194304,
        "capability_refreshed_utc": "2026-09-27T12:30:00+00:00",
        "capability_refresh_seconds": 30.0,
        "negotiated_packet_compression": "auto",
        "negotiated_packet_target_utilization": 0.72,
        "observed_packet_utilization": 0.76,
    }
    for _ in range(3):
        assert client.post("/api/v1/sensors/heartbeat", json=heartbeat).status_code == 200
    assert client.get("/api/v1/sensors/fleet").json()["attention_total"] == 1

    heartbeat["observed_packet_utilization"] = 0.70
    assert client.post("/api/v1/sensors/heartbeat", json=heartbeat).status_code == 200

    runtime = client.get("/api/v1/sensors/status").json()["sources"][0]
    assert runtime["packet_target_above_streak"] == 0
    assert runtime["packet_target_above_since_utc"] is None
    assert runtime["packet_target_above_seconds"] is None
    assert runtime["packet_target_last_above_utc"] is not None
    assert runtime["packet_target_sustained_episode_count"] == 1
    assert runtime["packet_target_last_recovered_utc"] is not None
    assert runtime["packet_target_recurrence_count"] == 0
    assert runtime["packet_target_last_recurrence_seconds"] is None
    fleet = client.get("/api/v1/sensors/fleet").json()
    assert fleet["packet_target"]["within_target"] == 1
    assert fleet["packet_target_sustained_episode_total"] == 1
    assert fleet["packet_target_recovered_sources"] == 1
    assert fleet["attention_total"] == 0


def test_packet_target_sustained_episode_count_increments_once_per_episode() -> None:
    client = TestClient(create_app(PerceptionPipeline()))
    heartbeat = {
        "schema_id": "visionrig/sensor-heartbeat/v6",
        "source_id": "kinect-repeat-pressure",
        "source_type": "camera",
        "negotiated_max_payload_bytes": 4194304,
        "capability_refreshed_utc": "2026-09-27T13:00:00+00:00",
        "capability_refresh_seconds": 30.0,
        "negotiated_packet_compression": "auto",
        "negotiated_packet_target_utilization": 0.72,
        "observed_packet_utilization": 0.76,
    }

    for _ in range(5):
        assert client.post("/api/v1/sensors/heartbeat", json=heartbeat).status_code == 200
    runtime = client.get("/api/v1/sensors/status").json()["sources"][0]
    assert runtime["packet_target_sustained_episode_count"] == 1

    heartbeat["observed_packet_utilization"] = 0.70
    assert client.post("/api/v1/sensors/heartbeat", json=heartbeat).status_code == 200
    runtime = client.get("/api/v1/sensors/status").json()["sources"][0]
    first_recovery = runtime["packet_target_last_recovered_utc"]
    assert first_recovery is not None

    heartbeat["observed_packet_utilization"] = 0.76
    for _ in range(3):
        assert client.post("/api/v1/sensors/heartbeat", json=heartbeat).status_code == 200

    runtime = client.get("/api/v1/sensors/status").json()["sources"][0]
    assert runtime["packet_target_sustained_episode_count"] == 2
    assert runtime["packet_target_last_recovered_utc"] == first_recovery
    assert runtime["packet_target_recurrence_count"] == 1
    assert runtime["packet_target_last_recurrence_seconds"] is not None

    fleet = client.get("/api/v1/sensors/fleet").json()
    assert fleet["packet_target_sustained_episode_total"] == 2
    assert fleet["packet_target_recovered_sources"] == 1
    assert fleet["packet_target_recurrence_total"] == 1
    assert fleet["packet_target_recurring_sources"] == 1
    assert fleet["packet_target_stability"] == {
        "stable": 0,
        "recurring": 0,
        "flapping": 1,
        "unknown": 0,
    }
    assert fleet["packet_target_flap_window_seconds"] == 120.0


def test_packet_target_stability_marks_delayed_recurrence_as_recurring() -> None:
    now = [datetime(2026, 9, 27, 14, 0, tzinfo=timezone.utc)]
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_clock=lambda: now[0],
            packet_target_flap_window_seconds=60.0,
        )
    )
    heartbeat = {
        "schema_id": "visionrig/sensor-heartbeat/v6",
        "source_id": "kinect-delayed-recurrence",
        "source_type": "camera",
        "negotiated_max_payload_bytes": 4194304,
        "capability_refreshed_utc": "2026-09-27T14:00:00+00:00",
        "capability_refresh_seconds": 30.0,
        "negotiated_packet_compression": "auto",
        "negotiated_packet_target_utilization": 0.72,
        "observed_packet_utilization": 0.76,
    }

    for _ in range(3):
        assert client.post("/api/v1/sensors/heartbeat", json=heartbeat).status_code == 200

    heartbeat["observed_packet_utilization"] = 0.70
    assert client.post("/api/v1/sensors/heartbeat", json=heartbeat).status_code == 200

    now[0] = datetime(2026, 9, 27, 14, 2, tzinfo=timezone.utc)
    heartbeat["observed_packet_utilization"] = 0.76
    for _ in range(3):
        assert client.post("/api/v1/sensors/heartbeat", json=heartbeat).status_code == 200

    runtime = client.get("/api/v1/sensors/status").json()["sources"][0]
    assert runtime["packet_target_recurrence_count"] == 1
    assert runtime["packet_target_last_recurrence_seconds"] == 120.0

    fleet = client.get("/api/v1/sensors/fleet").json()
    assert fleet["packet_target_flap_window_seconds"] == 60.0
    assert fleet["packet_target_stability"] == {
        "stable": 0,
        "recurring": 1,
        "flapping": 0,
        "unknown": 0,
    }


def test_packet_target_flap_window_must_be_positive() -> None:
    import pytest

    with pytest.raises(ValueError, match="packet_target_flap_window_seconds"):
        create_app(PerceptionPipeline(), packet_target_flap_window_seconds=0)


def test_catalog_exposes_flapping_packet_target_stability() -> None:
    now = [datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)]
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_clock=lambda: now[0],
            packet_target_flap_window_seconds=60.0,
        )
    )
    heartbeat = {
        "schema_id": "visionrig/sensor-heartbeat/v6",
        "source_id": "kinect-catalog-flap",
        "source_type": "camera",
        "negotiated_max_payload_bytes": 4194304,
        "capability_refreshed_utc": "2026-09-27T15:00:00+00:00",
        "capability_refresh_seconds": 30.0,
        "negotiated_packet_compression": "auto",
        "negotiated_packet_target_utilization": 0.72,
        "observed_packet_utilization": 0.76,
    }

    for _ in range(3):
        assert client.post("/api/v1/sensors/heartbeat", json=heartbeat).status_code == 200
    heartbeat["observed_packet_utilization"] = 0.70
    assert client.post("/api/v1/sensors/heartbeat", json=heartbeat).status_code == 200

    now[0] = datetime(2026, 9, 27, 15, 0, 30, tzinfo=timezone.utc)
    heartbeat["observed_packet_utilization"] = 0.76
    for _ in range(3):
        assert client.post("/api/v1/sensors/heartbeat", json=heartbeat).status_code == 200

    catalog = client.get("/api/v1/sensors/catalog").json()
    assert catalog["schema"] == "visionrig/sensor-catalog/v11"
    packet_target = catalog["sources"][0]["packet_target"]
    assert packet_target["status"] == "above_target"
    assert packet_target["pressure"] == "sustained"
    assert packet_target["stability"] == "flapping"
    assert packet_target["target_utilization"] == 0.72
    assert packet_target["observed_utilization"] == 0.76
    assert packet_target["overshoot_delta"] == 0.04
    assert packet_target["overshoot_ratio"] == 0.055556
    assert packet_target["attention_streak_threshold"] == 3
    assert packet_target["above_streak"] == 3
    assert packet_target["above_since_utc"] is not None
    assert packet_target["above_seconds"] == 0.0
    assert packet_target["last_above_utc"] is not None
    assert packet_target["sustained_episode_count"] == 2
    assert packet_target["last_recovered_utc"] is not None
    assert packet_target["flap_window_seconds"] == 60.0
    assert packet_target["recurrence_count"] == 1
    assert packet_target["last_recurrence_seconds"] == 30.0


def test_packet_target_overshoot_is_zero_within_target() -> None:
    client = TestClient(create_app(PerceptionPipeline()))
    response = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v6",
            "source_id": "kinect-overshoot-zero",
            "source_type": "camera",
            "negotiated_max_payload_bytes": 4194304,
            "capability_refreshed_utc": "2026-09-27T16:00:00+00:00",
            "capability_refresh_seconds": 30.0,
            "negotiated_packet_compression": "auto",
            "negotiated_packet_target_utilization": 0.72,
            "observed_packet_utilization": 0.70,
        },
    )
    assert response.status_code == 200

    catalog = client.get("/api/v1/sensors/catalog").json()
    packet_target = catalog["sources"][0]["packet_target"]
    assert packet_target["status"] == "within_target"
    assert packet_target["overshoot_delta"] == 0.0
    assert packet_target["overshoot_ratio"] == 0.0

    fleet = client.get("/api/v1/sensors/fleet").json()
    assert fleet["packet_target_overshoot"] == {
        "measured_sources": 1,
        "max_delta": 0.0,
        "max_ratio": 0.0,
    }
