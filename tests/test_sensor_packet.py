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
    describe_sensor_packet_transport,
    encode_sensor_packet,
    inspect_sensor_packet,
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
    header = inspect_sensor_packet(encoded)
    decoded = decode_sensor_packet(encoded)

    assert header.schema_id == "visionrig/sensor-packet/v1"
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


def test_sensor_packet_v2_compresses_and_roundtrips_planes() -> None:
    depth = np.zeros((120, 160), dtype=np.uint16)
    depth[40:80, 60:100] = 1750
    infrared = np.zeros((60, 80), dtype=np.uint16)
    infrared[10:20, 10:20] = 900

    raw = encode_sensor_packet(
        rgb_payload=b"jpeg-bytes",
        rgb_content_type="image/jpeg",
        depth_mm=depth,
        infrared=infrared,
        compression="none",
    )
    compressed = encode_sensor_packet(
        rgb_payload=b"jpeg-bytes",
        rgb_content_type="image/jpeg",
        depth_mm=depth,
        infrared=infrared,
        compression="zlib",
    )

    header = inspect_sensor_packet(compressed)
    decoded = decode_sensor_packet(compressed)

    assert header.schema_id == "visionrig/sensor-packet/v2"
    assert header.depth is not None
    assert header.depth.compression == "zlib"
    assert header.depth.raw_byte_length == depth.nbytes
    assert header.infrared is not None
    assert header.infrared.compression == "zlib"
    assert len(compressed) < len(raw)
    assert np.array_equal(decoded.depth_mm, depth)
    assert np.array_equal(decoded.infrared, infrared)


def test_sensor_packet_v2_ingress_matches_v1_semantics(monkeypatch) -> None:
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
    packet = encode_sensor_packet(
        rgb_payload=b"rgb-v2",
        rgb_content_type="image/jpeg",
        depth_mm=depth,
        compression="zlib",
    )

    response = client.post(
        "/api/v1/sensor-packets/ingest",
        params={
            "source_id": "compressed-kinect",
            "source_type": "camera",
            "frame_sequence": 1,
            "device": "kinect-v2",
        },
        content=packet,
        headers={"content-type": SENSOR_PACKET_MEDIA_TYPE},
    )

    assert response.status_code == 200
    assert probe.seen.payload == {
        "payload": b"rgb-v2",
        "content_type": "image/jpeg",
    }
    assert np.array_equal(probe.seen.sensor_data["depth_mm"], depth)
    assert registry.get_discovery("compressed-kinect").capabilities == (
        "depth",
        "rgb",
    )


def test_sensor_packet_v2_rejects_corrupt_zlib_plane() -> None:
    depth = np.zeros((8, 8), dtype=np.uint16)
    packet = bytearray(
        encode_sensor_packet(
            rgb_payload=b"jpeg",
            rgb_content_type="image/jpeg",
            depth_mm=depth,
            compression="zlib",
        )
    )
    packet[-1] ^= 0xFF

    try:
        decode_sensor_packet(bytes(packet))
    except SensorPacketError as exc:
        assert "zlib" in str(exc) or "decompressed" in str(exc)
    else:
        raise AssertionError("corrupt compressed plane must be rejected")


def test_sensor_packet_auto_compression_selects_per_plane() -> None:
    rng = np.random.default_rng(42)
    depth = np.zeros((120, 160), dtype=np.uint16)
    depth[30:90, 40:120] = 1850
    infrared = rng.integers(
        0,
        65536,
        size=(120, 160),
        dtype=np.uint16,
    )

    packet = encode_sensor_packet(
        rgb_payload=b"jpeg",
        rgb_content_type="image/jpeg",
        depth_mm=depth,
        infrared=infrared,
        compression="auto",
    )
    header = inspect_sensor_packet(packet)
    decoded = decode_sensor_packet(packet)

    assert header.schema_id == "visionrig/sensor-packet/v2"
    assert header.depth is not None
    assert header.depth.compression == "zlib"
    assert header.infrared is not None
    assert header.infrared.compression == "none"
    assert header.infrared.byte_length == infrared.nbytes
    assert header.infrared.raw_byte_length is None
    assert np.array_equal(decoded.depth_mm, depth)
    assert np.array_equal(decoded.infrared, infrared)


def test_sensor_packet_auto_stays_v2_even_when_plane_is_raw() -> None:
    rng = np.random.default_rng(7)
    depth = rng.integers(
        0,
        65536,
        size=(64, 64),
        dtype=np.uint16,
    )

    packet = encode_sensor_packet(
        rgb_payload=b"jpeg",
        rgb_content_type="image/jpeg",
        depth_mm=depth,
        compression="auto",
    )
    header = inspect_sensor_packet(packet)

    assert header.schema_id == "visionrig/sensor-packet/v2"
    assert header.depth is not None
    assert header.depth.compression == "none"
    assert decode_sensor_packet(packet).depth_mm.tolist() == depth.tolist()


def test_sensor_packet_transport_description_reports_wire_and_raw_bytes() -> None:
    depth = np.zeros((32, 32), dtype=np.uint16)
    depth[8:24, 8:24] = 1800
    infrared = np.arange(32 * 32, dtype=np.uint16).reshape(32, 32)

    packet = encode_sensor_packet(
        rgb_payload=b"jpeg-data",
        rgb_content_type="image/jpeg",
        depth_mm=depth,
        infrared=infrared,
        compression="auto",
    )
    telemetry = describe_sensor_packet_transport(packet)

    assert telemetry.schema == "visionrig/sensor-packet-transport/v2"
    assert telemetry.packet_schema == "visionrig/sensor-packet/v2"
    assert telemetry.packet_bytes == len(packet)
    assert telemetry.rgb_bytes == len(b"jpeg-data")
    assert telemetry.depth_raw_bytes == depth.nbytes
    assert telemetry.infrared_raw_bytes == infrared.nbytes
    assert telemetry.numeric_raw_bytes == depth.nbytes + infrared.nbytes
    assert telemetry.numeric_wire_bytes == (
        telemetry.depth_wire_bytes + telemetry.infrared_wire_bytes
    )
    assert telemetry.numeric_saved_bytes == (
        telemetry.numeric_raw_bytes - telemetry.numeric_wire_bytes
    )
    assert telemetry.numeric_compression_ratio == round(
        telemetry.numeric_wire_bytes / telemetry.numeric_raw_bytes,
        6,
    )
    assert telemetry.payload_utilization is None
    assert telemetry.payload_headroom_bytes is None
    assert telemetry.payload_status is None


def test_sensor_status_exposes_packet_transport_and_clears_on_plain_frame(
    monkeypatch,
) -> None:
    monkeypatch.setattr(sensor_ingress, "OpenCVImageDecoder", lambda: FakeCVDecoder())
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            max_sensor_frame_bytes=1024 * 1024,
        )
    )
    depth = np.zeros((32, 32), dtype=np.uint16)
    depth[8:24, 8:24] = 1800
    packet = encode_sensor_packet(
        rgb_payload=b"rgb",
        rgb_content_type="image/jpeg",
        depth_mm=depth,
        compression="auto",
    )

    response = client.post(
        "/api/v1/sensor-packets/ingest",
        params={
            "source_id": "transport-camera",
            "source_type": "camera",
            "frame_sequence": 1,
        },
        content=packet,
        headers={"content-type": SENSOR_PACKET_MEDIA_TYPE},
    )
    assert response.status_code == 200

    source = client.get("/api/v1/sensors/status").json()["sources"][0]
    telemetry = source["packet_transport"]
    assert telemetry["schema"] == "visionrig/sensor-packet-transport/v2"
    assert telemetry["packet_schema"] == "visionrig/sensor-packet/v2"
    assert telemetry["packet_bytes"] == len(packet)
    assert telemetry["depth_raw_bytes"] == depth.nbytes
    assert telemetry["numeric_saved_bytes"] > 0
    assert telemetry["numeric_compression_ratio"] < 1.0
    assert telemetry["payload_utilization"] == round(
        len(packet) / (1024 * 1024),
        6,
    )
    assert telemetry["payload_headroom_bytes"] == (1024 * 1024) - len(packet)
    assert telemetry["payload_status"] == "normal"

    plain = client.post(
        "/api/v1/frames/ingest",
        params={
            "source_id": "transport-camera",
            "source_type": "camera",
            "frame_sequence": 2,
        },
        content=b"plain-rgb",
        headers={"content-type": "image/jpeg"},
    )
    assert plain.status_code == 200

    refreshed = client.get("/api/v1/sensors/status").json()["sources"][0]
    assert refreshed["packet_transport"] is None


def test_sensor_packet_transport_payload_status_thresholds() -> None:
    packet = encode_sensor_packet(
        rgb_payload=b"x" * 4096,
        rgb_content_type="image/jpeg",
    )
    size = len(packet)

    normal = describe_sensor_packet_transport(
        packet,
        max_payload_bytes=size * 2,
    )
    warning = describe_sensor_packet_transport(
        packet,
        max_payload_bytes=max(size, int(size / 0.85)),
    )
    critical = describe_sensor_packet_transport(
        packet,
        max_payload_bytes=max(size, int(size / 0.97)),
    )

    assert normal.payload_status == "normal"
    assert normal.payload_utilization < 0.80
    assert warning.payload_status == "warning"
    assert 0.80 <= warning.payload_utilization < 0.95
    assert critical.payload_status == "critical"
    assert critical.payload_utilization >= 0.95
    assert critical.payload_headroom_bytes >= 0


def test_packet_transport_warning_enters_fleet_attention(monkeypatch) -> None:
    monkeypatch.setattr(sensor_ingress, "OpenCVImageDecoder", lambda: FakeCVDecoder())
    packet = encode_sensor_packet(
        rgb_payload=b"x" * 4096,
        rgb_content_type="image/jpeg",
    )
    max_payload = max(len(packet), (len(packet) * 100 + 84) // 85)
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            max_sensor_frame_bytes=max_payload,
        )
    )

    response = client.post(
        "/api/v1/sensor-packets/ingest",
        params={
            "source_id": "near-limit-camera",
            "source_type": "camera",
            "frame_sequence": 1,
        },
        content=packet,
        headers={"content-type": SENSOR_PACKET_MEDIA_TYPE},
    )
    assert response.status_code == 200

    runtime = client.get("/api/v1/sensors/status").json()["sources"][0]
    assert runtime["packet_transport"]["payload_status"] == "warning"
    assert 0.80 <= runtime["packet_transport"]["payload_utilization"] < 0.95

    fleet = client.get("/api/v1/sensors/fleet").json()
    assert fleet["schema"] == "visionrig/sensor-fleet-summary/v8"
    assert fleet["transport"] == {
        "normal": 0,
        "warning": 1,
        "critical": 0,
        "unknown": 0,
    }
    assert fleet["attention_total"] == 1
    item = fleet["attention"][0]
    assert item["source_id"] == "near-limit-camera"
    assert item["transport_status"] == "warning"
    assert item["reasons"] == ["packet_transport"]
    assert item["packet_transport"]["payload_status"] == "warning"


def test_sensor_packet_v2_can_carry_uncompressed_planes() -> None:
    depth = np.array([[1000, 1500], [2000, 2500]], dtype=np.uint16)
    packet = encode_sensor_packet(
        rgb_payload=b"jpeg",
        rgb_content_type="image/jpeg",
        depth_mm=depth,
        compression="none",
        packet_version="v2",
    )

    header = inspect_sensor_packet(packet)
    decoded = decode_sensor_packet(packet)

    assert header.schema_id == "visionrig/sensor-packet/v2"
    assert header.depth is not None
    assert header.depth.compression == "none"
    assert header.depth.raw_byte_length is None
    assert np.array_equal(decoded.depth_mm, depth)


def test_sensor_packet_v1_rejects_compressed_planes() -> None:
    depth = np.zeros((2, 2), dtype=np.uint16)
    try:
        encode_sensor_packet(
            rgb_payload=b"jpeg",
            rgb_content_type="image/jpeg",
            depth_mm=depth,
            compression="zlib",
            packet_version="v1",
        )
    except SensorPacketError as exc:
        assert "v1" in str(exc)
    else:
        raise AssertionError("SensorPacket/v1 compression must be rejected")
