import pytest

from visionrig.contracts import BoundingBox, SourceDescriptor, VisualEntity
from visionrig.kinect_v2 import KinectV2DepthStage, KinectV2FrameSet, KinectV2Source
from visionrig.pipeline import Frame, StageResult\nfrom visionrig.pipeline_factory import build_pipeline


class _Sampler:
    def __init__(self, distance: float | None) -> None:
        self.distance = distance
        self.calls: list[tuple[float, float]] = []

    def distance_m(self, x: float, y: float) -> float | None:
        self.calls.append((x, y))
        return self.distance


class _Backend:
    def __init__(self, frames: KinectV2FrameSet | None) -> None:
        self.frames = frames
        self.closed = False

    def read(self) -> KinectV2FrameSet | None:
        value, self.frames = self.frames, None
        return value

    def close(self) -> None:
        self.closed = True


def test_kinect_source_keeps_rgb_primary_and_aux_sensor_channels() -> None:
    sampler = _Sampler(2.5)
    backend = _Backend(
        KinectV2FrameSet(
            color_bgr={"rgb": True},
            depth_sampler=sampler,
            depth_mm={"depth": True},
            infrared={"ir": True},
        )
    )
    source = KinectV2Source(backend=backend)

    frame = source.read()
    assert frame is not None
    assert frame.payload == {"rgb": True}
    assert frame.source.device == "kinect-v2"
    assert frame.sensor_data["sensor_model"] == "kinect-v2"
    assert frame.sensor_data["metric_depth_sampler"] is sampler
    assert frame.sensor_data["depth_mm"] == {"depth": True}
    assert frame.sensor_data["infrared"] == {"ir": True}
    assert frame.sequence == 0
    assert source.read() is None

    source.close()
    assert backend.closed is True


def test_kinect_depth_stage_emits_measured_relative_depth() -> None:
    sampler = _Sampler(2.5)
    frame = Frame(
        source=SourceDescriptor(source_id="k", source_type="camera"),
        sequence=1,
        payload=None,
        sensor_data={"metric_depth_sampler": sampler},
    )
    entity = VisualEntity(
        entity_id="person-1",
        kind="person",
        label="person",
        confidence=0.9,
        bbox=BoundingBox(x=0.2, y=0.2, width=0.4, height=0.4),
    )

    result = KinectV2DepthStage().process(frame, StageResult(entities=(entity,)))

    assert len(result.depth) == 1
    assert result.depth[0].subject_entity_id == "person-1"
    assert result.depth[0].relative_depth == pytest.approx(0.5)
    assert result.depth[0].confidence == pytest.approx(1.0)
    assert result.depth[0].method == "kinect-v2-hardware-depth"
    assert len(sampler.calls) == 5


def test_kinect_depth_stage_is_noop_without_sampler() -> None:
    frame = Frame(
        source=SourceDescriptor(source_id="cam", source_type="camera"),
        sequence=1,
        payload=None,
    )
    current = StageResult()
    assert KinectV2DepthStage().process(frame, current) is current


def test_kinect_depth_range_must_be_valid() -> None:
    with pytest.raises(ValueError):
        KinectV2DepthStage(min_distance_m=2.0, max_distance_m=1.0)


def test_pipeline_can_enable_kinect_hardware_depth() -> None:
    bundle = build_pipeline(kinect_depth=True, spatial_relations=False)
    assert "kinect_v2_depth" in bundle.pipeline.stages
