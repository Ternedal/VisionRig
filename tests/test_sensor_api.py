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
        params={
            "source_id": "kaliv-screen",
            "source_type": "screen",
            "frame_sequence": 1,
        },
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

    status = client.get("/api/v1/sensors/status")
    assert status.status_code == 200
    body = status.json()
    assert body["schema"] == "visionrig/sensor-runtime-status/v1"
    assert body["accepted_total"] == 2
    assert body["active_processing"] is False
    assert len(body["sources"]) == 1
    source = body["sources"][0]
    assert source["source_id"] == "kaliv-vr"
    assert source["source_type"] == "vr"
    assert source["device"] == "quest"
    assert source["last_sequence"] == 5
    assert source["accepted_frames"] == 2
    assert source["dropped_frames_total"] == 3
    assert source["last_seen_utc"].endswith("+00:00")

    health = client.get("/health").json()
    assert health["schema"] == "visionrig/health/v5"
    assert health["sensor_ingress"]["runtime"]["accepted_total"] == 2


def test_sensor_status_counts_sequence_rejections(monkeypatch) -> None:
    monkeypatch.setattr(sensor_ingress, "OpenCVImageDecoder", lambda: FakeCVDecoder())
    client = TestClient(create_app(PerceptionPipeline(), max_sensor_frame_bytes=1024))

    params = {
        "source_id": "camera-a",
        "source_type": "camera",
        "frame_sequence": 7,
    }
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
        params={
            "source_id": "cam",
            "source_type": "camera",
            "frame_sequence": 1,
        },
        content=b"x" * 1025,
        headers={"content-type": "image/jpeg"},
    )
    assert response.status_code == 413


def test_raw_sensor_ingest_rejects_wrong_media_type(monkeypatch) -> None:
    monkeypatch.setattr(sensor_ingress, "OpenCVImageDecoder", lambda: FakeCVDecoder())
    client = TestClient(create_app(PerceptionPipeline(), max_sensor_frame_bytes=1024))
    response = client.post(
        "/api/v1/frames/ingest",
        params={
            "source_id": "cam",
            "source_type": "camera",
            "frame_sequence": 1,
        },
        content=b"x",
        headers={"content-type": "application/octet-stream"},
    )
    assert response.status_code == 415

    status = client.get("/api/v1/sensors/status").json()
    assert status["rejected_media_type_total"] == 1
