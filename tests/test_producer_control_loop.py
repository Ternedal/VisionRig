from types import SimpleNamespace

import numpy as np

from visionrig.contracts import SourceDescriptor
from visionrig.pipeline import Frame
from visionrig.producer_cli import _encode_kinect_packet, _run_controlled_capture
from visionrig.sensor_packet import decode_sensor_packet, inspect_sensor_packet


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeSource:
    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False
        self.reads = 0

    def read(self):
        self.reads += 1
        return SimpleNamespace(payload=f"frame-{self.name}-{self.reads}".encode())

    def close(self) -> None:
        self.closed = True


class FakeProducer:
    def __init__(self, enabled_states: list[bool]) -> None:
        self.enabled_states = list(enabled_states)
        self.state_index = 0
        self.heartbeats: list[tuple[tuple[str, ...], bool | None, int | None]] = []
        self.sent: list[bytes] = []
        self.sent_packets: list[bytes] = []

    def fetch_desired_state(self):
        index = min(self.state_index, len(self.enabled_states) - 1)
        enabled = self.enabled_states[index]
        self.state_index += 1
        return SimpleNamespace(enabled=enabled, revision=self.state_index)

    def send_heartbeat(
        self,
        *,
        capabilities: tuple[str, ...] = (),
        capture_active: bool | None = None,
        applied_revision: int | None = None,
    ):
        self.heartbeats.append((capabilities, capture_active, applied_revision))
        return SimpleNamespace(source_id="cam")

    def send_encoded(self, payload: bytes, *, content_type: str = "image/jpeg"):
        self.sent.append(payload)
        return SimpleNamespace(
            status="accepted",
            frame_sequence=len(self.sent) - 1,
        )

    def send_packet(self, payload: bytes):
        self.sent_packets.append(payload)
        return SimpleNamespace(
            status="accepted",
            frame_sequence=len(self.sent_packets) - 1,
        )

    def stats(self):
        return SimpleNamespace(
            pending_dropped_frames=0,
            accepted=len(self.sent),
        )


def test_disabled_source_is_not_opened_until_enabled() -> None:
    clock = FakeClock()
    producer = FakeProducer([False, True])
    sources: list[FakeSource] = []

    def source_factory() -> FakeSource:
        source = FakeSource(str(len(sources)))
        sources.append(source)
        return source

    frames = _run_controlled_capture(
        producer=producer,
        source_factory=source_factory,
        source_type="camera",
        capabilities=("rgb",),
        fps=5.0,
        jpeg_quality=80,
        max_frames=1,
        control_poll_seconds=1.0,
        verbose=False,
        encode_jpeg=lambda payload, _quality: payload,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert frames == 1
    assert len(sources) == 1
    assert sources[0].reads == 1
    assert sources[0].closed is True
    assert producer.heartbeats == [
        (("rgb",), False, 1),
        (("rgb",), True, 2),
    ]
    assert len(producer.sent) == 1


def test_enabled_to_disabled_closes_source_then_reopens_on_resume() -> None:
    clock = FakeClock()
    producer = FakeProducer([True, False, True])
    sources: list[FakeSource] = []

    def source_factory() -> FakeSource:
        source = FakeSource(str(len(sources)))
        sources.append(source)
        return source

    frames = _run_controlled_capture(
        producer=producer,
        source_factory=source_factory,
        source_type="camera",
        capabilities=("rgb",),
        fps=1.0,
        jpeg_quality=80,
        max_frames=2,
        control_poll_seconds=1.0,
        verbose=False,
        encode_jpeg=lambda payload, _quality: payload,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert frames == 2
    assert len(sources) == 2
    assert all(source.closed for source in sources)
    assert [source.reads for source in sources] == [1, 1]
    assert producer.heartbeats == [
        (("rgb",), True, 1),
        (("rgb",), False, 2),
        (("rgb",), True, 3),
    ]
    assert len(producer.sent) == 2


def test_paused_control_does_not_consume_frame_budget() -> None:
    clock = FakeClock()
    producer = FakeProducer([False, False, True])
    source = FakeSource("only")

    frames = _run_controlled_capture(
        producer=producer,
        source_factory=lambda: source,
        source_type="camera",
        capabilities=("rgb",),
        fps=2.0,
        jpeg_quality=80,
        max_frames=1,
        control_poll_seconds=0.5,
        verbose=False,
        encode_jpeg=lambda payload, _quality: payload,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert frames == 1
    assert source.reads == 1
    assert len(producer.sent) == 1
    assert len(producer.heartbeats) == 3


def test_controlled_capture_can_send_multimodal_packet() -> None:
    clock = FakeClock()
    producer = FakeProducer([True])
    source = FakeSource("kinect")

    frames = _run_controlled_capture(
        producer=producer,
        source_factory=lambda: source,
        source_type="camera",
        capabilities=("rgb", "depth", "infrared"),
        fps=5.0,
        jpeg_quality=80,
        max_frames=1,
        control_poll_seconds=1.0,
        verbose=False,
        encode_jpeg=lambda payload, _quality: b"jpeg:" + payload,
        packet_encoder=lambda frame, jpeg: b"packet:" + jpeg,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert frames == 1
    assert producer.sent == []
    assert producer.sent_packets == [b"packet:jpeg:frame-kinect-1"]
    assert producer.heartbeats == [
        (("rgb", "depth", "infrared"), True, 1),
    ]


def test_encode_kinect_packet_uses_aligned_depth_and_infrared() -> None:
    depth = np.array([[1000, 1500], [2000, 2500]], dtype=np.uint16)
    infrared = np.array([[10, 20], [30, 40]], dtype=np.uint16)
    frame = Frame(
        source=SourceDescriptor(
            source_id="kinect",
            source_type="camera",
            device="kinect-v2",
        ),
        sequence=0,
        payload=np.zeros((2, 2, 3), dtype=np.uint8),
        sensor_data={
            "color_aligned_depth_mm": depth,
            "infrared": infrared,
        },
    )

    packet = _encode_kinect_packet(frame, b"jpeg")
    header = inspect_sensor_packet(packet)
    decoded = decode_sensor_packet(packet)

    assert header.schema_id == "visionrig/sensor-packet/v2"
    assert header.depth is not None
    assert header.depth.compression in {"none", "zlib"}
    assert decoded.rgb_payload == b"jpeg"
    assert np.array_equal(decoded.depth_mm, depth)
    assert np.array_equal(decoded.infrared, infrared)
