from types import SimpleNamespace

from visionrig.producer_cli import _run_controlled_capture


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
        self.heartbeats: list[tuple[str, ...]] = []
        self.sent: list[bytes] = []

    def fetch_desired_state(self):
        index = min(self.state_index, len(self.enabled_states) - 1)
        enabled = self.enabled_states[index]
        self.state_index += 1
        return SimpleNamespace(enabled=enabled)

    def send_heartbeat(self, *, capabilities: tuple[str, ...] = ()):
        self.heartbeats.append(capabilities)
        return SimpleNamespace(source_id="cam")

    def send_encoded(self, payload: bytes, *, content_type: str = "image/jpeg"):
        self.sent.append(payload)
        return SimpleNamespace(
            status="accepted",
            frame_sequence=len(self.sent) - 1,
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
    assert producer.heartbeats == [("rgb",), ("rgb",)]
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
    assert producer.heartbeats == [("rgb",), ("rgb",), ("rgb",)]
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
