from datetime import datetime, timezone

from fastapi.testclient import TestClient

import pytest

import visionrig.sensor_ingress as sensor_ingress
from visionrig.api import create_app
from visionrig.pipeline import PerceptionPipeline
from visionrig.sensor_events import SensorChangeJournal


class _FailOnceReadinessJournal(SensorChangeJournal):
    def __init__(self) -> None:
        super().__init__()
        self.fail_readiness_once = True

    def append(self, **kwargs):
        if (
            kwargs.get("kind") == "producer_readiness_changed"
            and self.fail_readiness_once
        ):
            self.fail_readiness_once = False
            raise RuntimeError("simulated readiness journal persistence failure")
        return super().append(**kwargs)


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
    assert body["schema"] == "visionrig/sensor-runtime-status/v16"
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
    assert health["schema"] == "visionrig/health/v65"
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
    assert fleet["schema"] == "visionrig/sensor-fleet-summary/v31"
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
        "worst_source_id": "kinect-over-target",
        "worst_target_utilization": 0.72,
        "worst_observed_utilization": 0.76,
    }
    assert fleet["packet_target_sustained_pressure"]["sources"] == 1
    assert fleet["packet_target_sustained_pressure"]["longest_source_id"] == (
        "kinect-over-target"
    )
    assert fleet["packet_target_sustained_pressure"]["longest_seconds"] is not None
    assert fleet["packet_target_sustained_pressure"]["longest_since_utc"] is not None
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
    assert catalog["schema"] == "visionrig/sensor-catalog/v15"
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
        "worst_source_id": "kinect-overshoot-zero",
        "worst_target_utilization": 0.72,
        "worst_observed_utilization": 0.70,
    }


def test_packet_target_overshoot_identifies_worst_source() -> None:
    client = TestClient(create_app(PerceptionPipeline()))
    base = {
        "schema_id": "visionrig/sensor-heartbeat/v6",
        "source_type": "camera",
        "negotiated_max_payload_bytes": 4194304,
        "capability_refreshed_utc": "2026-09-27T16:30:00+00:00",
        "capability_refresh_seconds": 30.0,
        "negotiated_packet_compression": "auto",
        "negotiated_packet_target_utilization": 0.72,
    }

    for source_id, observed in (
        ("camera-low", 0.73),
        ("camera-high", 0.91),
        ("camera-ok", 0.70),
    ):
        payload = dict(base)
        payload["source_id"] = source_id
        payload["observed_packet_utilization"] = observed
        assert client.post("/api/v1/sensors/heartbeat", json=payload).status_code == 200

    fleet = client.get("/api/v1/sensors/fleet").json()
    assert fleet["schema"] == "visionrig/sensor-fleet-summary/v31"
    assert fleet["packet_target_overshoot"] == {
        "measured_sources": 3,
        "max_delta": 0.19,
        "max_ratio": 0.263889,
        "worst_source_id": "camera-high",
        "worst_target_utilization": 0.72,
        "worst_observed_utilization": 0.91,
    }


def test_fleet_reports_longest_sustained_packet_pressure() -> None:
    now = [datetime(2026, 9, 27, 17, 0, tzinfo=timezone.utc)]
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_clock=lambda: now[0],
        )
    )
    base = {
        "schema_id": "visionrig/sensor-heartbeat/v6",
        "source_type": "camera",
        "negotiated_max_payload_bytes": 4194304,
        "capability_refreshed_utc": "2026-09-27T17:00:00+00:00",
        "capability_refresh_seconds": 30.0,
        "negotiated_packet_compression": "auto",
        "negotiated_packet_target_utilization": 0.72,
        "observed_packet_utilization": 0.80,
    }

    old = dict(base)
    old["source_id"] = "camera-old-pressure"
    for _ in range(3):
        assert client.post("/api/v1/sensors/heartbeat", json=old).status_code == 200

    now[0] = datetime(2026, 9, 27, 17, 0, 20, tzinfo=timezone.utc)
    newer = dict(base)
    newer["source_id"] = "camera-new-pressure"
    for _ in range(3):
        assert client.post("/api/v1/sensors/heartbeat", json=newer).status_code == 200

    now[0] = datetime(2026, 9, 27, 17, 1, 0, tzinfo=timezone.utc)
    fleet = client.get("/api/v1/sensors/fleet").json()
    assert fleet["schema"] == "visionrig/sensor-fleet-summary/v31"
    assert fleet["packet_target_sustained_pressure"] == {
        "sources": 2,
        "longest_seconds": 60.0,
        "longest_source_id": "camera-old-pressure",
        "longest_since_utc": "2026-09-27T17:00:00+00:00",
    }


def test_fleet_identifies_most_recurrent_packet_target_source() -> None:
    client = TestClient(create_app(PerceptionPipeline()))
    base = {
        "schema_id": "visionrig/sensor-heartbeat/v6",
        "source_type": "camera",
        "negotiated_max_payload_bytes": 4194304,
        "capability_refreshed_utc": "2026-09-27T18:00:00+00:00",
        "capability_refresh_seconds": 30.0,
        "negotiated_packet_compression": "auto",
        "negotiated_packet_target_utilization": 0.72,
    }

    def sustained(source_id: str) -> None:
        payload = dict(base)
        payload["source_id"] = source_id
        payload["observed_packet_utilization"] = 0.80
        for _ in range(3):
            assert client.post("/api/v1/sensors/heartbeat", json=payload).status_code == 200

    def recover(source_id: str) -> None:
        payload = dict(base)
        payload["source_id"] = source_id
        payload["observed_packet_utilization"] = 0.70
        assert client.post("/api/v1/sensors/heartbeat", json=payload).status_code == 200

    sustained("camera-one-repeat")
    recover("camera-one-repeat")
    sustained("camera-one-repeat")

    sustained("camera-two-repeats")
    recover("camera-two-repeats")
    sustained("camera-two-repeats")
    recover("camera-two-repeats")
    sustained("camera-two-repeats")

    fleet = client.get("/api/v1/sensors/fleet").json()
    assert fleet["schema"] == "visionrig/sensor-fleet-summary/v31"
    assert fleet["packet_target_recurrence_total"] == 3
    assert fleet["packet_target_recurring_sources"] == 2
    assert fleet["packet_target_recurrence_hotspot"]["max_recurrence_count"] == 2
    assert fleet["packet_target_recurrence_hotspot"]["source_id"] == (
        "camera-two-repeats"
    )
    assert (
        fleet["packet_target_recurrence_hotspot"]["last_recurrence_seconds"]
        is not None
    )


def test_fleet_reports_latest_packet_target_recovery() -> None:
    now = [datetime(2026, 9, 27, 19, 0, tzinfo=timezone.utc)]
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_clock=lambda: now[0],
        )
    )
    base = {
        "schema_id": "visionrig/sensor-heartbeat/v6",
        "source_type": "camera",
        "negotiated_max_payload_bytes": 4194304,
        "capability_refreshed_utc": "2026-09-27T19:00:00+00:00",
        "capability_refresh_seconds": 30.0,
        "negotiated_packet_compression": "auto",
        "negotiated_packet_target_utilization": 0.72,
    }

    def sustained(source_id: str) -> None:
        payload = dict(base)
        payload["source_id"] = source_id
        payload["observed_packet_utilization"] = 0.80
        for _ in range(3):
            assert client.post("/api/v1/sensors/heartbeat", json=payload).status_code == 200

    def recover(source_id: str) -> None:
        payload = dict(base)
        payload["source_id"] = source_id
        payload["observed_packet_utilization"] = 0.70
        assert client.post("/api/v1/sensors/heartbeat", json=payload).status_code == 200

    sustained("camera-first-recovery")
    recover("camera-first-recovery")

    now[0] = datetime(2026, 9, 27, 19, 2, tzinfo=timezone.utc)
    sustained("camera-latest-recovery")
    recover("camera-latest-recovery")

    fleet = client.get("/api/v1/sensors/fleet").json()
    assert fleet["schema"] == "visionrig/sensor-fleet-summary/v31"
    assert fleet["packet_target_recovered_sources"] == 2
    assert fleet["packet_target_latest_recovery"] == {
        "source_id": "camera-latest-recovery",
        "recovered_utc": "2026-09-27T19:02:00+00:00",
        "age_seconds": 0.0,
    }

    now[0] = datetime(2026, 9, 27, 19, 3, 30, tzinfo=timezone.utc)
    later = client.get("/api/v1/sensors/fleet").json()
    assert later["packet_target_latest_recovery"]["source_id"] == (
        "camera-latest-recovery"
    )
    assert later["packet_target_latest_recovery"]["age_seconds"] == 90.0


def test_packet_target_stability_requires_complete_measurement() -> None:
    client = TestClient(create_app(PerceptionPipeline()))

    target_only = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v5",
            "source_id": "camera-target-only",
            "source_type": "camera",
            "negotiated_max_payload_bytes": 4194304,
            "capability_refreshed_utc": "2026-09-27T20:00:00+00:00",
            "capability_refresh_seconds": 30.0,
            "negotiated_packet_compression": "auto",
            "negotiated_packet_target_utilization": 0.72,
        },
    )
    assert target_only.status_code == 200

    measured = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v6",
            "source_id": "camera-measured",
            "source_type": "camera",
            "negotiated_max_payload_bytes": 4194304,
            "capability_refreshed_utc": "2026-09-27T20:00:00+00:00",
            "capability_refresh_seconds": 30.0,
            "negotiated_packet_compression": "auto",
            "negotiated_packet_target_utilization": 0.72,
            "observed_packet_utilization": 0.70,
        },
    )
    assert measured.status_code == 200

    fleet = client.get("/api/v1/sensors/fleet").json()
    assert fleet["schema"] == "visionrig/sensor-fleet-summary/v31"
    assert fleet["packet_target_stability"] == {
        "stable": 1,
        "recurring": 0,
        "flapping": 0,
        "unknown": 1,
    }
    assert fleet["packet_target_measurement_coverage"] == {
        "complete": 1,
        "target_only": 1,
        "unavailable": 0,
    }
    assert fleet["heartbeat_schema_coverage"] == {
        "runtime_sources": 2,
        "v2": 0,
        "v3": 0,
        "v4": 0,
        "v5": 1,
        "v6": 1,
        "no_heartbeat": 0,
        "upgrade_required": 1,
    }
    assert fleet["heartbeat_upgrade_candidate_total"] == 1
    assert fleet["heartbeat_upgrade_candidates_truncated"] is False
    assert fleet["producer_readiness"] == {
        "runtime_sources": 2,
        "heartbeat_v6_sources": 1,
        "heartbeat_upgrade_required": 1,
        "heartbeat_upgrade_stage_counts": {
            "contract_upgrade": 1,
            "establish_heartbeat": 0,
        },
        "heartbeat_v6_ratio": 0.5,
        "packet_measurement_complete_sources": 1,
        "packet_measurement_gap_sources": 1,
        "packet_measurement_complete_ratio": 0.5,
    }
    assert fleet["heartbeat_upgrade_candidates"] == [
        {
            "source_id": "camera-target-only",
            "presence": "online",
            "current_schema_id": "visionrig/sensor-heartbeat/v5",
            "required_schema_id": "visionrig/sensor-heartbeat/v6",
            "measurement": "target_only",
            "upgrade_stage": "contract_upgrade",
            "versions_behind": 1,
        }
    ]
    assert fleet["packet_target_measurement_gap_total"] == 1
    assert fleet["packet_target_measurement_gaps_truncated"] is False
    assert fleet["packet_target_measurement_gaps"] == [
        {
            "source_id": "camera-target-only",
            "measurement": "target_only",
            "presence": "online",
            "heartbeat_schema_id": "visionrig/sensor-heartbeat/v5",
            "required_schema_id": "visionrig/sensor-heartbeat/v6",
            "negotiated_packet_target_utilization": 0.72,
            "observed_packet_utilization": None,
        }
    ]
    assert fleet["attention_total"] == 0

    catalog = client.get("/api/v1/sensors/catalog").json()
    by_id = {source["source_id"]: source for source in catalog["sources"]}
    assert by_id["camera-target-only"]["runtime"]["heartbeat_schema_id"] == (
        "visionrig/sensor-heartbeat/v5"
    )
    assert by_id["camera-measured"]["runtime"]["heartbeat_schema_id"] == (
        "visionrig/sensor-heartbeat/v6"
    )
    assert by_id["camera-target-only"]["producer_readiness"] == {
        "runtime_available": True,
        "heartbeat_v6": False,
        "heartbeat_upgrade_required": True,
        "heartbeat_schema_id": "visionrig/sensor-heartbeat/v5",
        "required_heartbeat_schema_id": "visionrig/sensor-heartbeat/v6",
        "heartbeat_versions_behind": 1,
        "heartbeat_upgrade_stage": "contract_upgrade",
        "packet_measurement_complete": False,
        "packet_measurement": "target_only",
    }
    assert by_id["camera-measured"]["producer_readiness"] == {
        "runtime_available": True,
        "heartbeat_v6": True,
        "heartbeat_upgrade_required": False,
        "heartbeat_schema_id": "visionrig/sensor-heartbeat/v6",
        "required_heartbeat_schema_id": "visionrig/sensor-heartbeat/v6",
        "heartbeat_versions_behind": None,
        "heartbeat_upgrade_stage": None,
        "packet_measurement_complete": True,
        "packet_measurement": "complete",
    }
    assert by_id["camera-target-only"]["packet_target"]["measurement"] == (
        "target_only"
    )
    assert by_id["camera-target-only"]["packet_target"]["stability"] == "unknown"
    assert by_id["camera-measured"]["packet_target"]["measurement"] == "complete"
    assert by_id["camera-measured"]["packet_target"]["stability"] == "stable"


def test_packet_target_measurement_gaps_are_bounded_and_deterministic() -> None:
    client = TestClient(create_app(PerceptionPipeline()))

    for index in range(40):
        response = client.post(
            "/api/v1/sensors/heartbeat",
            json={
                "schema_id": "visionrig/sensor-heartbeat/v5",
                "source_id": f"camera-gap-{index:02d}",
                "source_type": "camera",
                "negotiated_max_payload_bytes": 4194304,
                "capability_refreshed_utc": "2026-09-27T20:30:00+00:00",
                "capability_refresh_seconds": 30.0,
                "negotiated_packet_compression": "auto",
                "negotiated_packet_target_utilization": 0.72,
            },
        )
        assert response.status_code == 200

    fleet = client.get("/api/v1/sensors/fleet").json()
    assert fleet["schema"] == "visionrig/sensor-fleet-summary/v31"
    assert fleet["packet_target_measurement_gap_total"] == 40
    assert fleet["heartbeat_schema_coverage"] == {
        "runtime_sources": 40,
        "v2": 0,
        "v3": 0,
        "v4": 0,
        "v5": 40,
        "v6": 0,
        "no_heartbeat": 0,
        "upgrade_required": 40,
    }
    assert fleet["heartbeat_upgrade_candidate_total"] == 40
    assert fleet["producer_readiness"] == {
        "runtime_sources": 40,
        "heartbeat_v6_sources": 0,
        "heartbeat_upgrade_required": 40,
        "heartbeat_upgrade_stage_counts": {
            "contract_upgrade": 40,
            "establish_heartbeat": 0,
        },
        "heartbeat_v6_ratio": 0.0,
        "packet_measurement_complete_sources": 0,
        "packet_measurement_gap_sources": 40,
        "packet_measurement_complete_ratio": 0.0,
    }
    assert len(fleet["heartbeat_upgrade_candidates"]) == 32
    assert fleet["heartbeat_upgrade_candidates_truncated"] is True
    assert fleet["heartbeat_upgrade_candidates"][0]["source_id"] == "camera-gap-00"
    assert fleet["heartbeat_upgrade_candidates"][-1]["source_id"] == "camera-gap-31"
    assert all(
        item["current_schema_id"] == "visionrig/sensor-heartbeat/v5"
        for item in fleet["heartbeat_upgrade_candidates"]
    )
    assert all(
        item["upgrade_stage"] == "contract_upgrade"
        for item in fleet["heartbeat_upgrade_candidates"]
    )
    assert all(
        item["versions_behind"] == 1
        for item in fleet["heartbeat_upgrade_candidates"]
    )
    assert len(fleet["packet_target_measurement_gaps"]) == 32
    assert fleet["packet_target_measurement_gaps_truncated"] is True
    assert fleet["packet_target_measurement_gaps"][0]["source_id"] == "camera-gap-00"
    assert fleet["packet_target_measurement_gaps"][-1]["source_id"] == "camera-gap-31"
    assert all(
        item["heartbeat_schema_id"] == "visionrig/sensor-heartbeat/v5"
        for item in fleet["packet_target_measurement_gaps"]
    )
    assert all(
        item["required_schema_id"] == "visionrig/sensor-heartbeat/v6"
        for item in fleet["packet_target_measurement_gaps"]
    )
    assert fleet["attention_total"] == 0


def test_heartbeat_upgrade_candidates_prioritize_versions_behind() -> None:
    client = TestClient(create_app(PerceptionPipeline()))

    payloads = (
        {
            "schema_id": "visionrig/sensor-heartbeat/v5",
            "source_id": "camera-a-v5",
            "source_type": "camera",
            "negotiated_max_payload_bytes": 4194304,
            "capability_refreshed_utc": "2026-09-27T21:00:00+00:00",
            "capability_refresh_seconds": 30.0,
            "negotiated_packet_compression": "auto",
            "negotiated_packet_target_utilization": 0.72,
        },
        {
            "schema_id": "visionrig/sensor-heartbeat/v2",
            "source_id": "camera-z-v2",
            "source_type": "camera",
        },
        {
            "schema_id": "visionrig/sensor-heartbeat/v4",
            "source_id": "camera-m-v4",
            "source_type": "camera",
            "negotiated_max_payload_bytes": 4194304,
            "capability_refreshed_utc": "2026-09-27T21:00:00+00:00",
            "capability_refresh_seconds": 30.0,
            "negotiated_packet_compression": "auto",
        },
    )
    for payload in payloads:
        assert client.post("/api/v1/sensors/heartbeat", json=payload).status_code == 200

    fleet = client.get("/api/v1/sensors/fleet").json()
    assert fleet["schema"] == "visionrig/sensor-fleet-summary/v31"
    candidates = fleet["heartbeat_upgrade_candidates"]
    assert [item["source_id"] for item in candidates] == [
        "camera-z-v2",
        "camera-m-v4",
        "camera-a-v5",
    ]
    assert [item["versions_behind"] for item in candidates] == [4, 2, 1]
    assert all(
        item["upgrade_stage"] == "contract_upgrade"
        for item in candidates
    )
    assert fleet["heartbeat_upgrade_candidate_total"] == 3
    assert fleet["heartbeat_upgrade_candidates_truncated"] is False
    assert fleet["attention_total"] == 0


def test_producer_readiness_transition_changes_only_on_readiness_change() -> None:
    now = [datetime(2026, 9, 28, 4, 0, tzinfo=timezone.utc)]
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_clock=lambda: now[0],
        )
    )

    v5 = {
        "schema_id": "visionrig/sensor-heartbeat/v5",
        "source_id": "camera-migrate",
        "source_type": "camera",
        "negotiated_max_payload_bytes": 4194304,
        "capability_refreshed_utc": "2026-09-28T04:00:00+00:00",
        "capability_refresh_seconds": 30.0,
        "negotiated_packet_compression": "auto",
        "negotiated_packet_target_utilization": 0.72,
    }
    assert client.post("/api/v1/sensors/heartbeat", json=v5).status_code == 200

    baseline = client.get("/api/v1/sensors/fleet").json()
    assert baseline["schema"] == "visionrig/sensor-fleet-summary/v31"
    assert baseline["producer_readiness"]["heartbeat_v6_ratio"] == 0.0
    assert baseline["producer_readiness_transition"] == {
        "previous": None,
        "changed_utc": None,
        "heartbeat_v6_sources_delta": None,
        "heartbeat_v6_ratio_delta": None,
        "packet_measurement_complete_sources_delta": None,
        "packet_measurement_complete_ratio_delta": None,
    }
    baseline_changes = client.get("/api/v1/sensors/changes").json()
    baseline_cursor = baseline_changes["next_cursor"]
    assert "producer_readiness_changed" not in [
        entry["event"]["kind"] for entry in baseline_changes["entries"]
    ]

    now[0] = datetime(2026, 9, 28, 4, 5, tzinfo=timezone.utc)
    v6 = dict(v5)
    v6["schema_id"] = "visionrig/sensor-heartbeat/v6"
    v6["capability_refreshed_utc"] = "2026-09-28T04:05:00+00:00"
    v6["observed_packet_utilization"] = 0.70
    assert client.post("/api/v1/sensors/heartbeat", json=v6).status_code == 200

    # The transition timestamp belongs to the runtime mutation, not the first
    # dashboard poll that observes it.
    now[0] = datetime(2026, 9, 28, 4, 7, tzinfo=timezone.utc)
    migrated = client.get("/api/v1/sensors/fleet").json()
    assert migrated["producer_readiness"] == {
        "runtime_sources": 1,
        "heartbeat_v6_sources": 1,
        "heartbeat_upgrade_required": 0,
        "heartbeat_upgrade_stage_counts": {
            "contract_upgrade": 0,
            "establish_heartbeat": 0,
        },
        "heartbeat_v6_ratio": 1.0,
        "packet_measurement_complete_sources": 1,
        "packet_measurement_gap_sources": 0,
        "packet_measurement_complete_ratio": 1.0,
    }
    transition = migrated["producer_readiness_transition"]
    assert transition["previous"] == {
        "runtime_sources": 1,
        "heartbeat_v6_sources": 0,
        "heartbeat_upgrade_required": 1,
        "heartbeat_upgrade_stage_counts": {
            "contract_upgrade": 1,
            "establish_heartbeat": 0,
        },
        "heartbeat_v6_ratio": 0.0,
        "packet_measurement_complete_sources": 0,
        "packet_measurement_gap_sources": 1,
        "packet_measurement_complete_ratio": 0.0,
    }
    assert transition["changed_utc"] == "2026-09-28T04:05:00+00:00"
    assert transition["heartbeat_v6_sources_delta"] == 1
    assert transition["heartbeat_v6_ratio_delta"] == 1.0
    assert transition["packet_measurement_complete_sources_delta"] == 1
    assert transition["packet_measurement_complete_ratio_delta"] == 1.0

    readiness_changes = client.get(
        "/api/v1/sensors/changes",
        params={"after_cursor": baseline_cursor},
    ).json()
    readiness_events = [
        entry["event"]
        for entry in readiness_changes["entries"]
        if entry["event"]["kind"] == "producer_readiness_changed"
    ]
    assert len(readiness_events) == 1
    readiness_event = readiness_events[0]
    assert readiness_event["source_id"] == "camera-migrate"
    assert readiness_event["occurred_utc"] == "2026-09-28T04:05:00+00:00"
    assert readiness_event["payload"]["previous"] == transition["previous"]
    assert readiness_event["payload"]["current"] == migrated["producer_readiness"]
    assert readiness_event["payload"]["transition"] == transition

    event_cursor = readiness_changes["next_cursor"]
    now[0] = datetime(2026, 9, 28, 4, 10, tzinfo=timezone.utc)
    repeated_heartbeat = dict(v6)
    repeated_heartbeat["capability_refreshed_utc"] = "2026-09-28T04:10:00+00:00"
    assert (
        client.post("/api/v1/sensors/heartbeat", json=repeated_heartbeat).status_code
        == 200
    )
    quiet = client.get(
        "/api/v1/sensors/changes",
        params={"after_cursor": event_cursor},
    ).json()
    assert "producer_readiness_changed" not in [
        entry["event"]["kind"] for entry in quiet["entries"]
    ]

    repeated = client.get("/api/v1/sensors/fleet").json()
    assert repeated["producer_readiness_transition"] == transition

    health = client.get("/health").json()
    assert health["sensor_fleet"]["producer_readiness_transition"] == transition


def test_producer_readiness_transition_retries_after_journal_failure() -> None:
    now = [datetime(2026, 9, 28, 4, 0, tzinfo=timezone.utc)]
    journal = _FailOnceReadinessJournal()
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_clock=lambda: now[0],
            sensor_change_journal=journal,
        )
    )

    v5 = {
        "schema_id": "visionrig/sensor-heartbeat/v5",
        "source_id": "camera-retry-event",
        "source_type": "camera",
        "negotiated_max_payload_bytes": 4194304,
        "capability_refreshed_utc": "2026-09-28T04:00:00+00:00",
        "capability_refresh_seconds": 30.0,
        "negotiated_packet_compression": "auto",
        "negotiated_packet_target_utilization": 0.72,
    }
    assert client.post("/api/v1/sensors/heartbeat", json=v5).status_code == 200

    now[0] = datetime(2026, 9, 28, 4, 5, tzinfo=timezone.utc)
    v6 = dict(v5)
    v6["schema_id"] = "visionrig/sensor-heartbeat/v6"
    v6["capability_refreshed_utc"] = "2026-09-28T04:05:00+00:00"
    v6["observed_packet_utilization"] = 0.70

    with pytest.raises(
        RuntimeError,
        match="simulated readiness journal persistence failure",
    ):
        client.post("/api/v1/sensors/heartbeat", json=v6)

    after_failure = client.get("/api/v1/sensors/fleet").json()
    assert after_failure["producer_readiness"]["heartbeat_v6_sources"] == 1
    assert after_failure["producer_readiness_transition"] == {
        "previous": None,
        "changed_utc": None,
        "heartbeat_v6_sources_delta": None,
        "heartbeat_v6_ratio_delta": None,
        "packet_measurement_complete_sources_delta": None,
        "packet_measurement_complete_ratio_delta": None,
    }

    retry = client.post("/api/v1/sensors/heartbeat", json=v6)
    assert retry.status_code == 200

    events = client.get("/api/v1/sensors/changes").json()["entries"]
    readiness = [
        entry["event"]
        for entry in events
        if entry["event"]["kind"] == "producer_readiness_changed"
    ]
    assert len(readiness) == 1
    assert readiness[0]["source_id"] == "camera-retry-event"
    assert readiness[0]["occurred_utc"] == "2026-09-28T04:05:00+00:00"


def test_fleet_upgrade_candidate_matches_catalog_producer_readiness() -> None:
    client = TestClient(create_app(PerceptionPipeline()))
    heartbeat = {
        "schema_id": "visionrig/sensor-heartbeat/v5",
        "source_id": "camera-shared-readiness",
        "source_type": "camera",
        "negotiated_max_payload_bytes": 4194304,
        "capability_refreshed_utc": "2026-09-28T05:00:00+00:00",
        "capability_refresh_seconds": 30.0,
        "negotiated_packet_compression": "auto",
        "negotiated_packet_target_utilization": 0.72,
    }
    assert client.post("/api/v1/sensors/heartbeat", json=heartbeat).status_code == 200

    fleet = client.get("/api/v1/sensors/fleet").json()
    catalog = client.get("/api/v1/sensors/catalog").json()
    candidate = fleet["heartbeat_upgrade_candidates"][0]
    readiness = catalog["sources"][0]["producer_readiness"]

    assert candidate["source_id"] == "camera-shared-readiness"
    assert candidate["current_schema_id"] == readiness["heartbeat_schema_id"]
    assert (
        candidate["required_schema_id"]
        == readiness["required_heartbeat_schema_id"]
    )
    assert candidate["versions_behind"] == readiness["heartbeat_versions_behind"]
    assert candidate["upgrade_stage"] == readiness["heartbeat_upgrade_stage"]
    assert candidate["measurement"] == readiness["packet_measurement"]
    assert readiness["heartbeat_upgrade_required"] is True
    assert readiness["packet_measurement_complete"] is False


def test_online_producer_readiness_excludes_offline_runtime_sources() -> None:
    now = [datetime(2026, 9, 28, 6, 0, tzinfo=timezone.utc)]
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_clock=lambda: now[0],
            sensor_stale_after_seconds=15.0,
            sensor_offline_after_seconds=60.0,
        )
    )

    offline_v6 = {
        "schema_id": "visionrig/sensor-heartbeat/v6",
        "source_id": "camera-old-v6",
        "source_type": "camera",
        "negotiated_max_payload_bytes": 4194304,
        "capability_refreshed_utc": "2026-09-28T06:00:00+00:00",
        "capability_refresh_seconds": 30.0,
        "negotiated_packet_compression": "auto",
        "negotiated_packet_target_utilization": 0.72,
        "observed_packet_utilization": 0.70,
    }
    assert (
        client.post("/api/v1/sensors/heartbeat", json=offline_v6).status_code
        == 200
    )

    now[0] = datetime(2026, 9, 28, 6, 2, tzinfo=timezone.utc)
    online_v5 = {
        "schema_id": "visionrig/sensor-heartbeat/v5",
        "source_id": "camera-current-v5",
        "source_type": "camera",
        "negotiated_max_payload_bytes": 4194304,
        "capability_refreshed_utc": "2026-09-28T06:02:00+00:00",
        "capability_refresh_seconds": 30.0,
        "negotiated_packet_compression": "auto",
        "negotiated_packet_target_utilization": 0.72,
    }
    assert (
        client.post("/api/v1/sensors/heartbeat", json=online_v5).status_code
        == 200
    )

    fleet = client.get("/api/v1/sensors/fleet").json()
    assert fleet["schema"] == "visionrig/sensor-fleet-summary/v31"
    assert fleet["presence"] == {
        "online": 1,
        "stale": 0,
        "offline": 1,
        "unknown": 0,
    }
    assert fleet["producer_readiness"] == {
        "runtime_sources": 2,
        "heartbeat_v6_sources": 1,
        "heartbeat_upgrade_required": 1,
        "heartbeat_upgrade_stage_counts": {
            "contract_upgrade": 1,
            "establish_heartbeat": 0,
        },
        "heartbeat_v6_ratio": 0.5,
        "packet_measurement_complete_sources": 1,
        "packet_measurement_gap_sources": 1,
        "packet_measurement_complete_ratio": 0.5,
    }
    assert fleet["online_producer_readiness"] == {
        "runtime_sources": 1,
        "heartbeat_v6_sources": 0,
        "heartbeat_upgrade_required": 1,
        "heartbeat_upgrade_stage_counts": {
            "contract_upgrade": 1,
            "establish_heartbeat": 0,
        },
        "heartbeat_v6_ratio": 0.0,
        "packet_measurement_complete_sources": 0,
        "packet_measurement_gap_sources": 1,
        "packet_measurement_complete_ratio": 0.0,
    }
    assert fleet["online_producer_readiness_expiry"] == {
        "next_change_utc": "2026-09-28T06:02:15.001000+00:00",
        "next_change_seconds": 15.001,
        "source_id": "camera-current-v5",
    }

    now[0] = datetime(2026, 9, 28, 6, 2, 10, tzinfo=timezone.utc)
    aging = client.get("/api/v1/sensors/fleet").json()
    assert aging["online_producer_readiness_expiry"] == {
        "next_change_utc": "2026-09-28T06:02:15.001000+00:00",
        "next_change_seconds": 5.001,
        "source_id": "camera-current-v5",
    }

    now[0] = datetime(2026, 9, 28, 6, 2, 15, tzinfo=timezone.utc)
    boundary = client.get("/api/v1/sensors/fleet").json()
    assert boundary["online_producer_readiness"]["runtime_sources"] == 1
    assert boundary["online_producer_readiness_expiry"] == {
        "next_change_utc": "2026-09-28T06:02:15.001000+00:00",
        "next_change_seconds": 0.001,
        "source_id": "camera-current-v5",
    }

    now[0] = datetime(2026, 9, 28, 6, 2, 16, tzinfo=timezone.utc)
    stale = client.get("/api/v1/sensors/fleet").json()
    assert stale["online_producer_readiness"]["runtime_sources"] == 0
    assert stale["online_producer_readiness_expiry"] == {
        "next_change_utc": None,
        "next_change_seconds": None,
        "source_id": None,
    }

    health = client.get("/health").json()
    assert health["sensor_fleet"]["online_producer_readiness"] == (
        stale["online_producer_readiness"]
    )
    assert health["sensor_fleet"]["online_producer_readiness_expiry"] == (
        stale["online_producer_readiness_expiry"]
    )


def test_producer_readiness_distinguishes_missing_heartbeat_blocker(monkeypatch) -> None:
    monkeypatch.setattr(sensor_ingress, "OpenCVImageDecoder", lambda: FakeCVDecoder())
    client = TestClient(
        create_app(PerceptionPipeline(), max_sensor_frame_bytes=1024)
    )

    frame_only = client.post(
        "/api/v1/frames/ingest",
        params={
            "source_id": "screen-frame-only",
            "source_type": "screen",
            "frame_sequence": 0,
        },
        content=b"encoded-frame",
        headers={"content-type": "image/jpeg"},
    )
    assert frame_only.status_code == 200

    v5 = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v5",
            "source_id": "camera-v5",
            "source_type": "camera",
            "negotiated_max_payload_bytes": 4194304,
            "capability_refreshed_utc": "2026-09-28T07:00:00+00:00",
            "capability_refresh_seconds": 30.0,
            "negotiated_packet_compression": "auto",
            "negotiated_packet_target_utilization": 0.72,
        },
    )
    assert v5.status_code == 200

    fleet = client.get("/api/v1/sensors/fleet").json()
    assert fleet["schema"] == "visionrig/sensor-fleet-summary/v31"
    assert fleet["producer_readiness"]["heartbeat_upgrade_required"] == 2
    assert fleet["producer_readiness"]["heartbeat_upgrade_stage_counts"] == {
        "contract_upgrade": 1,
        "establish_heartbeat": 1,
    }
    assert fleet["online_producer_readiness"]["heartbeat_upgrade_stage_counts"] == {
        "contract_upgrade": 1,
        "establish_heartbeat": 1,
    }
    by_id = {
        item["source_id"]: item
        for item in fleet["heartbeat_upgrade_candidates"]
    }
    assert by_id["camera-v5"]["upgrade_stage"] == "contract_upgrade"
    assert by_id["screen-frame-only"]["upgrade_stage"] == "establish_heartbeat"
    assert fleet["attention_total"] == 0
