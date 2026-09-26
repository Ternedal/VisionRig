import numpy as np
from fastapi.testclient import TestClient

import visionrig.sensor_ingress as sensor_ingress
from visionrig.api import create_app
from visionrig.pipeline import PerceptionPipeline, StageResult
from visionrig.sensor_packet import (
    SENSOR_PACKET_MEDIA_TYPE,
    ArrayMetricDepthSampler,
    SensorPacketError,
    decode_sensor_packet,
    encode_sensor_packet,
)
from visionrig.sensor_registry import SensorRegistry


class FakeCVDecoder:
    def decode(self, payload: bytes, content_type: str):
        return {"payload": payload, "content_type": content_type}


class PacketProbeStage:
    name = "packet_probe"

    def __init__(self) -> None:
        self.seen = None

    def process(self, frame, current):
        self.seen = frame
        return StageResult(
            entities=current.entities,
            relations=current.relations,
            landmarks=current.landmarks,
            depth=current.depth,
            scene_label="packet-ok",
            scene_confidence=1.0,
        )


def test_sensor_packet_roundtrip_preserves_rgb_depth_and_infrared() -> None:
    depth = np.array([[1000, 1500, 0], [2000, 2500, 3000]], dtype=np.uint16)
    infrared = np.array([[10, 20, 30], [40, 50, 60]], dtype=np.uint16)

    encoded = encode_sensor_packet(
        rgb_payload=b"jpeg-bytes",
        rgb_content_type="image/jpeg",
        depth_mm=depth,
        infrared=infrared,
    )
    decoded = decode_sensor_packet(encoded)

    assert decoded.rgb_content_type == "image/jpeg"
    assert decoded.rgb_payload == b"jpeg-bytes"
    assert np.array_equal(decoded.depth_mm, depth)
    assert np.array_equal(decoded.infrared, infrared)

    sampler = ArrayMetricDepthSampler(decoded.depth_mm)
    assert sampler.distance_m(0.0, 0.0) == 1.0
    assert sampler.distance_m(1.0, 1.0) == 3.0
    assert sampler.distance_m(1.0, 0.0) is None


def test_sensor_packet_rejects_trailing_bytes() -> None:
    encoded = encode_sensor_packet(
        rgb_payload=b"jpeg",
        rgb_content_type="image/jpeg",
    )
    try:
        decode_sensor_packet(encoded + b"x")
    except SensorPacketError as exc:
        assert "trailing bytes" in str(exc)
    else:
        raise AssertionError("trailing bytes must be rejected")


def test_multimodal_packet_ingress_populates_frame_local_sensor_data(monkeypatch) -> None:
    monkeypatch.setattr(sensor_ingress, "OpenCVImageDecoder", lambda: FakeCVDecoder())
    registry = SensorRegistry()
    probe = PacketProbeStage()
    client = TestClient(
        create_app(
            PerceptionPipeline((probe,)),
            sensor_registry=registry,
            max_sensor_frame_bytes=1024 * 1024,
        )
    )
    depth = np.array([[1000, 1500], [2000, 2500]], dtype=np.uint16)
    infrared = np.array([[1, 2], [3, 4]], dtype=np.uint16)
    packet = encode_sensor_packet(
        rgb_payload=b"rgb",
        rgb_content_type="image/jpeg",
        depth_mm=depth,
        infrared=infrared,
    )

    response = client.post(
        "/api/v1/sensor-packets/ingest",
        params={
            "source_id": "remote-kinect",
            "source_type": "camera",
            "frame_sequence": 9,
            "device": "kinect-v2",
        },
        content=packet,
        headers={"content-type": SENSOR_PACKET_MEDIA_TYPE},
    )

    assert response.status_code == 200
    assert response.json()["frame_sequence"] == 9
    assert probe.seen is not None
    assert probe.seen.payload == {
        "payload": b"rgb",
        "content_type": "image/jpeg",
    }
    assert np.array_equal(probe.seen.sensor_data["depth_mm"], depth)
    assert np.array_equal(probe.seen.sensor_data["infrared"], infrared)
    assert (
        probe.seen.sensor_data["metric_depth_sampler"].distance_m(0.0, 0.0)
        == 1.0
    )

    discovery = registry.get_discovery("remote-kinect")
    assert discovery is not None
    assert discovery.device == "kinect-v2"
    assert discovery.capabilities == ("depth", "infrared", "rgb")

    events = client.get("/api/v1/perception/events").json()
    assert events["entries"][0]["event"]["scene_label"] == "packet-ok"


def test_rgb_only_packet_declares_only_rgb(monkeypatch) -> None:
    monkeypatch.setattr(sensor_ingress, "OpenCVImageDecoder", lambda: FakeCVDecoder())
    registry = SensorRegistry()
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_registry=registry,
            max_sensor_frame_bytes=1024 * 1024,
        )
    )
    packet = encode_sensor_packet(
        rgb_payload=b"rgb",
        rgb_content_type="image/jpeg",
    )

    response = client.post(
        "/api/v1/sensor-packets/ingest",
        params={
            "source_id": "rgb-only",
            "source_type": "camera",
            "frame_sequence": 0,
        },
        content=packet,
        headers={"content-type": SENSOR_PACKET_MEDIA_TYPE},
    )
    assert response.status_code == 200
    assert registry.get_discovery("rgb-only").capabilities == ("rgb",)


def test_malformed_packet_does_not_register_source(monkeypatch) -> None:
    monkeypatch.setattr(sensor_ingress, "OpenCVImageDecoder", lambda: FakeCVDecoder())
    registry = SensorRegistry()
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_registry=registry,
            max_sensor_frame_bytes=1024 * 1024,
        )
    )

    response = client.post(
        "/api/v1/sensor-packets/ingest",
        params={
            "source_id": "broken-kinect",
            "source_type": "camera",
            "frame_sequence": 0,
        },
        content=b"not-a-packet",
        headers={"content-type": SENSOR_PACKET_MEDIA_TYPE},
    )

    assert response.status_code == 422
    assert registry.contains("broken-kinect") is False
    assert client.get("/api/v1/perception/events").json()["entries"] == []


def test_packet_source_type_conflict_is_rejected_before_pipeline(monkeypatch) -> None:
    monkeypatch.setattr(sensor_ingress, "OpenCVImageDecoder", lambda: FakeCVDecoder())
    registry = SensorRegistry()
    registry.observe("shared-id", source_type="vr", device="quest")
    probe = PacketProbeStage()
    client = TestClient(
        create_app(
            PerceptionPipeline((probe,)),
            sensor_registry=registry,
            max_sensor_frame_bytes=1024 * 1024,
        )
    )
    packet = encode_sensor_packet(
        rgb_payload=b"rgb",
        rgb_content_type="image/jpeg",
    )

    response = client.post(
        "/api/v1/sensor-packets/ingest",
        params={
            "source_id": "shared-id",
            "source_type": "camera",
            "frame_sequence": 0,
        },
        content=packet,
        headers={"content-type": SENSOR_PACKET_MEDIA_TYPE},
    )

    assert response.status_code == 409
    assert probe.seen is None
