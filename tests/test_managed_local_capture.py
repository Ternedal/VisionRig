from types import SimpleNamespace

from visionrig.capture_cli import _run_capture
from visionrig.contracts import SourceDescriptor
from visionrig.pipeline import Frame


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeSource:
    def __init__(self, name: str, clock: FakeClock) -> None:
        self.name = name
        self.clock = clock
        self.source = SourceDescriptor(
            source_id="kinect-v2-0",
            source_type="camera",
            device="kinect-v2",
        )
        self.closed = False
        self.local_sequence = 0

    def read(self):
        if self.closed:
            return None
        sequence = self.local_sequence
        self.local_sequence += 1
        self.clock.advance(1.0)
        return Frame(
            source=self.source,
            sequence=sequence,
            payload={"source": self.name},
            sensor_data={"sensor_model": "kinect-v2"},
        )

    def close(self) -> None:
        self.closed = True


class FakeRuntime:
    def __init__(self) -> None:
        self.pending = None
        self.sequences: list[int] = []
        self.sensor_models: list[str] = []

    def submit(self, frame: Frame) -> None:
        self.pending = frame

    def process_next(self):
        frame = self.pending
        self.pending = None
        self.sequences.append(frame.sequence)
        self.sensor_models.append(frame.sensor_data["sensor_model"])
        return SimpleNamespace(
            frame_sequence=frame.sequence,
            entities=(),
            landmarks=(),
            depth=(),
            dropped_frames=frame.dropped_frames,
        )


class FakeControl:
    def __init__(self, enabled_states: list[bool]) -> None:
        self.enabled_states = enabled_states
        self.index = 0
        self.heartbeats: list[tuple[tuple[str, ...], bool | None, int | None]] = []

    def fetch_desired_state(self):
        index = min(self.index, len(self.enabled_states) - 1)
        enabled = self.enabled_states[index]
        self.index += 1
        return SimpleNamespace(enabled=enabled, revision=self.index)

    def send_heartbeat(
        self,
        *,
        capabilities: tuple[str, ...] = (),
        capture_active: bool | None = None,
        applied_revision: int | None = None,
    ):
        self.heartbeats.append((capabilities, capture_active, applied_revision))
        return SimpleNamespace(source_id="kinect-v2-0")


def test_managed_capture_does_not_open_kinect_while_disabled() -> None:
    clock = FakeClock()
    runtime = FakeRuntime()
    control = FakeControl([False, True])
    sources: list[FakeSource] = []

    def source_factory() -> FakeSource:
        source = FakeSource(str(len(sources)), clock)
        sources.append(source)
        return source

    processed, _ = _run_capture(
        runtime=runtime,
        source_factory=source_factory,
        max_frames=1,
        verbose=False,
        control=control,
        capabilities=("rgb", "depth", "infrared"),
        control_poll_seconds=1.0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert processed == 1
    assert len(sources) == 1
    assert sources[0].closed is True
    assert control.heartbeats == [
        (("rgb", "depth", "infrared"), False, 1),
        (("rgb", "depth", "infrared"), True, 2),
    ]


def test_managed_capture_reopens_source_and_keeps_logical_sequence_monotonic() -> None:
    clock = FakeClock()
    runtime = FakeRuntime()
    control = FakeControl([True, False, True])
    sources: list[FakeSource] = []

    def source_factory() -> FakeSource:
        source = FakeSource(str(len(sources)), clock)
        sources.append(source)
        return source

    processed, _ = _run_capture(
        runtime=runtime,
        source_factory=source_factory,
        max_frames=2,
        verbose=False,
        control=control,
        capabilities=("rgb", "depth", "infrared"),
        control_poll_seconds=1.0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert processed == 2
    assert len(sources) == 2
    assert all(source.closed for source in sources)
    assert runtime.sequences == [0, 1]
    assert runtime.sensor_models == ["kinect-v2", "kinect-v2"]
    assert control.heartbeats == [
        (("rgb", "depth", "infrared"), True, 1),
        (("rgb", "depth", "infrared"), False, 2),
        (("rgb", "depth", "infrared"), True, 3),
    ]


def test_unmanaged_local_capture_preserves_source_sequence() -> None:
    clock = FakeClock()
    runtime = FakeRuntime()
    source = FakeSource("local", clock)
    source.local_sequence = 41

    processed, _ = _run_capture(
        runtime=runtime,
        source_factory=lambda: source,
        max_frames=2,
        verbose=False,
        control=None,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert processed == 2
    assert runtime.sequences == [41, 42]
    assert source.closed is True
