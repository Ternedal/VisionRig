from types import SimpleNamespace

import numpy as np
import pytest

from visionrig.contracts import SourceDescriptor
from visionrig.pipeline import Frame
from visionrig.producer_cli import (
    PacketTransportPlan,
    _encode_kinect_packet,
    _encode_packet_with_budget,
    _run_controlled_capture,
    _select_packet_compression,
)
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
        self.negotiation_heartbeats: list[
            tuple[int | None, str | None, float | None, str | None]
        ] = []
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
        negotiated_max_payload_bytes: int | None = None,
        capability_refreshed_utc: str | None = None,
        capability_refresh_seconds: float | None = None,
        negotiated_packet_compression: str | None = None,
    ):
        self.heartbeats.append((capabilities, capture_active, applied_revision))
        self.negotiation_heartbeats.append(
            (
                negotiated_max_payload_bytes,
                capability_refreshed_utc,
                capability_refresh_seconds,
                negotiated_packet_compression,
            )
        )
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


def test_packet_budget_adapts_jpeg_quality_below_warning_threshold() -> None:
    frame = SimpleNamespace(payload=b"frame")
    qualities: list[int] = []

    def encode_jpeg(_payload, quality: int) -> bytes:
        qualities.append(quality)
        return b"x" * (quality * 10)

    packet, quality, utilization = _encode_packet_with_budget(
        frame=frame,
        packet_encoder=lambda _frame, jpeg: b"h" * 100 + jpeg,
        encode_jpeg=encode_jpeg,
        initial_quality=80,
        min_quality=30,
        max_packet_bytes=1024,
    )

    assert quality == 70
    assert qualities == [80, 75, 70]
    assert len(packet) == 800
    assert utilization == 800 / 1024
    assert utilization < 0.80


def test_packet_budget_accepts_irreducible_warning_but_not_overflow() -> None:
    frame = SimpleNamespace(payload=b"frame")

    packet, quality, utilization = _encode_packet_with_budget(
        frame=frame,
        packet_encoder=lambda _frame, jpeg: b"h" * 850 + jpeg,
        encode_jpeg=lambda _payload, q: b"x" * q,
        initial_quality=80,
        min_quality=30,
        max_packet_bytes=1024,
    )

    assert quality == 30
    assert len(packet) == 880
    assert utilization == 880 / 1024


def test_packet_budget_failure_happens_before_send_and_sequence_reservation() -> None:
    clock = FakeClock()
    producer = FakeProducer([True])
    source = FakeSource("kinect")

    try:
        _run_controlled_capture(
            producer=producer,
            source_factory=lambda: source,
            source_type="camera",
            capabilities=("rgb", "depth", "infrared"),
            fps=5.0,
            jpeg_quality=80,
            max_frames=1,
            control_poll_seconds=1.0,
            verbose=False,
            encode_jpeg=lambda _payload, q: b"x" * q,
            packet_encoder=lambda _frame, jpeg: b"h" * 1000 + jpeg,
            packet_budget_bytes=1024,
            min_jpeg_quality=30,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
    except RuntimeError as exc:
        assert "exceeds producer packet budget" in str(exc)
    else:
        raise AssertionError("oversized packet must fail before send")

    assert producer.sent_packets == []
    assert source.closed is True


def test_capability_refresh_updates_packet_budget_between_frames() -> None:
    clock = FakeClock()
    producer = FakeProducer([True, True])
    source = FakeSource("kinect")
    budgets = iter([2000, 1024])
    qualities: list[int] = []

    def encode_jpeg(_payload, quality: int) -> bytes:
        qualities.append(quality)
        return b"x" * (quality * 10)

    frames = _run_controlled_capture(
        producer=producer,
        source_factory=lambda: source,
        source_type="camera",
        capabilities=("rgb", "depth", "infrared"),
        fps=1.0,
        jpeg_quality=80,
        max_frames=2,
        control_poll_seconds=1.0,
        verbose=False,
        encode_jpeg=encode_jpeg,
        packet_encoder=lambda _frame, jpeg: b"h" * 100 + jpeg,
        packet_budget_provider=lambda: next(budgets),
        capability_refresh_seconds=1.0,
        min_jpeg_quality=30,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert frames == 2
    assert qualities == [80, 80, 75, 70]
    assert [len(packet) for packet in producer.sent_packets] == [900, 800]
    assert producer.negotiation_heartbeats[0][0] == 2000
    assert producer.negotiation_heartbeats[0][1] is not None
    assert producer.negotiation_heartbeats[0][2] == 1.0
    assert producer.negotiation_heartbeats[1][0] == 1024
    assert source.closed is True


def test_capability_refresh_failure_closes_capture_before_next_frame() -> None:
    clock = FakeClock()
    producer = FakeProducer([True, True])
    source = FakeSource("kinect")
    calls = 0

    def budget_provider() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return 2048
        raise RuntimeError("capability refresh failed")

    try:
        _run_controlled_capture(
            producer=producer,
            source_factory=lambda: source,
            source_type="camera",
            capabilities=("rgb", "depth", "infrared"),
            fps=1.0,
            jpeg_quality=80,
            max_frames=2,
            control_poll_seconds=1.0,
            verbose=False,
            encode_jpeg=lambda _payload, q: b"x" * q,
            packet_encoder=lambda _frame, jpeg: b"h" * 100 + jpeg,
            packet_budget_provider=budget_provider,
            capability_refresh_seconds=1.0,
            min_jpeg_quality=30,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
    except RuntimeError as exc:
        assert "capability refresh failed" in str(exc)
    else:
        raise AssertionError("capability refresh failure must stop capture")

    assert len(producer.sent_packets) == 1
    assert source.reads == 1
    assert source.closed is True


def test_packet_budget_provider_requires_packet_encoder() -> None:
    clock = FakeClock()
    producer = FakeProducer([True])

    try:
        _run_controlled_capture(
            producer=producer,
            source_factory=lambda: FakeSource("camera"),
            source_type="camera",
            capabilities=("rgb",),
            fps=1.0,
            jpeg_quality=80,
            max_frames=1,
            control_poll_seconds=1.0,
            verbose=False,
            packet_budget_provider=lambda: 2048,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
    except ValueError as exc:
        assert "requires packet_encoder" in str(exc)
    else:
        raise AssertionError("packet budget provider without packet encoder must fail")


def test_select_packet_compression_uses_only_negotiated_modes() -> None:
    assert _select_packet_compression(("none", "zlib")) == "auto"
    assert _select_packet_compression(("zlib",)) == "zlib"
    assert _select_packet_compression(("none",)) == "none"


def test_encode_kinect_packet_raw_mode_stays_sensor_packet_v2() -> None:
    depth = np.array([[1000, 1500], [2000, 2500]], dtype=np.uint16)
    frame = Frame(
        source=SourceDescriptor(
            source_id="kinect",
            source_type="camera",
            device="kinect-v2",
        ),
        sequence=0,
        payload=np.zeros((2, 2, 3), dtype=np.uint8),
        sensor_data={"color_aligned_depth_mm": depth},
    )

    packet = _encode_kinect_packet(
        frame,
        b"jpeg",
        compression="none",
    )
    header = inspect_sensor_packet(packet)

    assert header.schema_id == "visionrig/sensor-packet/v2"
    assert header.depth is not None
    assert header.depth.compression == "none"


def test_live_transport_refresh_can_change_packet_encoder_strategy() -> None:
    clock = FakeClock()
    producer = FakeProducer([True, True])
    source = FakeSource("kinect")
    plans = iter(
        [
            PacketTransportPlan(
                max_payload_bytes=2048,
                compression="auto",
                target_utilization=0.80,
                packet_encoder=lambda _frame, jpeg: b"auto:" + jpeg,
            ),
            PacketTransportPlan(
                max_payload_bytes=2048,
                compression="none",
                target_utilization=0.70,
                packet_encoder=lambda _frame, jpeg: b"raw:" + jpeg,
            ),
        ]
    )

    frames = _run_controlled_capture(
        producer=producer,
        source_factory=lambda: source,
        source_type="camera",
        capabilities=("rgb", "depth", "infrared"),
        fps=1.0,
        jpeg_quality=80,
        max_frames=2,
        control_poll_seconds=1.0,
        verbose=False,
        encode_jpeg=lambda payload, _quality: b"jpeg:" + payload,
        packet_encoder=lambda _frame, jpeg: b"initial:" + jpeg,
        packet_transport_provider=lambda: next(plans),
        capability_refresh_seconds=1.0,
        min_jpeg_quality=30,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert frames == 2
    assert producer.sent_packets == [
        b"auto:jpeg:frame-kinect-1",
        b"raw:jpeg:frame-kinect-2",
    ]
    assert producer.negotiation_heartbeats[0][0] == 2048
    assert producer.negotiation_heartbeats[0][3] == "auto"
    assert producer.negotiation_heartbeats[1][0] == 2048
    assert producer.negotiation_heartbeats[1][3] == "none"


def test_select_packet_compression_rejects_unknown_only_modes() -> None:
    with pytest.raises(Exception, match="no supported SensorPacket compression"):
        _select_packet_compression(("future-codec",))


def test_packet_budget_uses_negotiated_target_utilization() -> None:
    frame = SimpleNamespace(payload=b"frame")

    packet, quality, utilization = _encode_packet_with_budget(
        frame=frame,
        packet_encoder=lambda _frame, jpeg: b"h" * 100 + jpeg,
        encode_jpeg=lambda _payload, q: b"x" * (q * 10),
        initial_quality=80,
        min_quality=30,
        max_packet_bytes=2000,
        target_utilization=0.50,
    )

    assert quality == 80
    assert len(packet) == 900
    assert utilization == 0.45

    packet2, quality2, utilization2 = _encode_packet_with_budget(
        frame=frame,
        packet_encoder=lambda _frame, jpeg: b"h" * 100 + jpeg,
        encode_jpeg=lambda _payload, q: b"x" * (q * 10),
        initial_quality=80,
        min_quality=30,
        max_packet_bytes=1000,
        target_utilization=0.60,
    )

    assert quality2 == 45
    assert len(packet2) == 550
    assert utilization2 == 0.55


def test_transport_refresh_can_change_target_utilization() -> None:
    clock = FakeClock()
    producer = FakeProducer([True, True])
    source = FakeSource("kinect")
    plans = iter(
        [
            PacketTransportPlan(
                max_payload_bytes=1000,
                compression="auto",
                target_utilization=0.80,
                packet_encoder=lambda _frame, jpeg: b"h" * 100 + jpeg,
            ),
            PacketTransportPlan(
                max_payload_bytes=1000,
                compression="auto",
                target_utilization=0.60,
                packet_encoder=lambda _frame, jpeg: b"h" * 100 + jpeg,
            ),
        ]
    )
    qualities: list[int] = []

    frames = _run_controlled_capture(
        producer=producer,
        source_factory=lambda: source,
        source_type="camera",
        capabilities=("rgb", "depth", "infrared"),
        fps=1.0,
        jpeg_quality=80,
        max_frames=2,
        control_poll_seconds=1.0,
        verbose=False,
        encode_jpeg=lambda _payload, q: (
            qualities.append(q) or (b"x" * (q * 10))
        ),
        packet_encoder=lambda _frame, jpeg: jpeg,
        packet_transport_provider=lambda: next(plans),
        capability_refresh_seconds=1.0,
        min_jpeg_quality=30,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert frames == 2
    assert qualities == [80, 75, 70, 65, 80, 75, 70, 65, 60, 55, 50, 45]
    assert [len(packet) for packet in producer.sent_packets] == [750, 550]
