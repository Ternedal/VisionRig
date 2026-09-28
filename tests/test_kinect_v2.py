import numpy as np
import pytest

from visionrig.contracts import BoundingBox, SourceDescriptor, VisualEntity
from visionrig.infrared import InfraredSummaryStage
from visionrig.kinect_v2 import (
    KinectV2DepthStage,
    KinectV2FrameSet,
    KinectV2Source,
    align_depth_mm_to_color,
)
from visionrig.pipeline import Frame, PerceptionPipeline, StageResult
from visionrig.pipeline_factory import build_pipeline
from visionrig.spatial import SpatialRelationStage


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
            color_aligned_depth_mm={"aligned": True},
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
    assert frame.sensor_data["color_aligned_depth_mm"] == {"aligned": True}
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
    assert result.depth[0].distance_m == pytest.approx(2.5)
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


def test_align_depth_mm_to_color_maps_valid_points_and_zeroes_invalid() -> None:
    depth = np.array(
        [
            [1000, 1100, 1200],
            [2000, 2100, 2200],
        ],
        dtype=np.uint16,
    )
    mapping = np.array(
        [
            [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]],
            [[0.0, 1.0], [2.0, 1.0], [99.0, 99.0]],
        ],
        dtype=np.float32,
    )

    aligned = align_depth_mm_to_color(
        mapping,
        depth,
        color_width=3,
        color_height=2,
    )

    assert aligned.dtype == np.uint16
    assert aligned.tolist() == [
        [1000, 1100, 1200],
        [2000, 2200, 0],
    ]


def test_align_depth_mm_to_color_rejects_wrong_mapping_shape() -> None:
    depth = np.zeros((2, 3), dtype=np.uint16)
    mapping = np.zeros((1, 1, 2), dtype=np.float32)

    with pytest.raises(Exception, match="mapping shape"):
        align_depth_mm_to_color(
            mapping,
            depth,
            color_width=3,
            color_height=2,
        )


def test_infrared_summary_stage_emits_normalized_statistics() -> None:
    infrared = np.array(
        [[0, 16384], [32768, 65535]],
        dtype=np.uint16,
    )
    frame = Frame(
        source=SourceDescriptor(source_id="k", source_type="camera"),
        sequence=2,
        payload=None,
        sensor_data={"infrared": infrared},
    )

    result = InfraredSummaryStage().process(frame, StageResult())

    assert len(result.infrared) == 1
    observation = result.infrared[0]
    assert observation.mean_intensity == pytest.approx(
        np.mean(infrared.astype(np.float64) / 65535.0)
    )
    assert observation.contrast == pytest.approx(
        np.std(infrared.astype(np.float64) / 65535.0)
    )
    assert observation.hotspot_fraction == pytest.approx(0.25)
    assert observation.sample_count == 4
    assert observation.method == "kinect-v2-infrared-summary"


def test_infrared_summary_stage_is_noop_without_valid_plane() -> None:
    stage = InfraredSummaryStage()
    frame = Frame(
        source=SourceDescriptor(source_id="cam", source_type="camera"),
        sequence=1,
        payload=None,
    )
    current = StageResult()
    assert stage.process(frame, current) is current

    invalid = Frame(
        source=frame.source,
        sequence=2,
        payload=None,
        sensor_data={"infrared": []},
    )
    assert stage.process(invalid, current) is current


def test_pipeline_enables_infrared_summary_by_default() -> None:
    bundle = build_pipeline(spatial_relations=False)
    assert "infrared_summary" in bundle.pipeline.stages


def test_infrared_summary_bounds_large_plane_sample_count() -> None:
    infrared = np.zeros((2049, 2048), dtype=np.uint16)
    frame = Frame(
        source=SourceDescriptor(source_id="k-large", source_type="camera"),
        sequence=3,
        payload=None,
        sensor_data={"infrared": infrared},
    )

    result = InfraredSummaryStage().process(frame, StageResult())

    assert len(result.infrared) == 1
    assert 1 <= result.infrared[0].sample_count <= 4_194_304


def test_infrared_observation_survives_later_pipeline_stage() -> None:
    infrared = np.full((2, 2), 32768, dtype=np.uint16)
    frame = Frame(
        source=SourceDescriptor(source_id="k-compose", source_type="camera"),
        sequence=4,
        payload=None,
        sensor_data={"infrared": infrared},
    )
    pipeline = PerceptionPipeline(
        (
            InfraredSummaryStage(),
            SpatialRelationStage(),
        )
    )

    event = pipeline.process(frame)

    assert len(event.infrared) == 1
    assert event.infrared[0].sample_count == 4
