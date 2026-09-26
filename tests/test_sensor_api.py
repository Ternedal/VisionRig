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
    assert body["schema"] == "visionrig/sensor-runtime-status/v4"
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
    assert source["last_seen_utc"].endswith("+00:00")

    health = client.get("/health").json()
    assert health["schema"] == "visionrig/health/v15"
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
