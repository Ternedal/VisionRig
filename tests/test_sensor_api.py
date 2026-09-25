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
