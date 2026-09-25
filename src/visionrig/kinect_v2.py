"""Microsoft Kinect v2 source adapter.

The adapter uses the optional kinect-next package and keeps that dependency out
of VisionRig core. RGB remains the primary Frame payload so existing stages
continue to work unchanged. Hardware depth and infrared are carried as
frame-local sensor data.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Protocol

from .contracts import DepthObservation, SourceDescriptor
from .pipeline import Frame, StageResult


class KinectV2Unavailable(RuntimeError):
    pass


class KinectV2ReadError(RuntimeError):
    pass


class MetricDepthSampler(Protocol):
    def distance_m(self, x: float, y: float) -> float | None: ...


@dataclass(frozen=True, slots=True)
class KinectV2FrameSet:
    color_bgr: Any
    depth_sampler: MetricDepthSampler | None = None
    depth_mm: Any | None = None
    infrared: Any | None = None


class KinectV2Backend(Protocol):
    def read(self) -> KinectV2FrameSet | None: ...
    def close(self) -> None: ...


class _KinectNextDepthSampler:
    """Sample metric depth at normalized color-image coordinates."""

    def __init__(self, sensor: Any, depth_frame: Any, *, width: int, height: int) -> None:
        self._sensor = sensor
        self._depth_frame = depth_frame
        self._width = width
        self._height = height
        self._mapping: Any | None = None

    def _ensure_mapping(self) -> Any:
        if self._mapping is None:
            self._mapping = self._sensor.mapper.map_color_frame_to_depth_space(
                self._depth_frame
            )
        return self._mapping

    def distance_m(self, x: float, y: float) -> float | None:
        if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
            return None
        mapping = self._ensure_mapping()
        px = min(self._width - 1, max(0, int(round(x * (self._width - 1)))))
        py = min(self._height - 1, max(0, int(round(y * (self._height - 1)))))
        point = mapping[py, px]
        try:
            dx = float(point[0])
            dy = float(point[1])
        except (TypeError, ValueError, IndexError):
            return None
        if not (dx == dx and dy == dy):
            return None
        depth_width = int(getattr(self._depth_frame, "width", 512))
        depth_height = int(getattr(self._depth_frame, "height", 424))
        ix = int(round(dx))
        iy = int(round(dy))
        if ix < 0 or iy < 0 or ix >= depth_width or iy >= depth_height:
            return None
        try:
            distance = float(self._depth_frame.distance_at(ix, iy))
        except (IndexError, TypeError, ValueError):
            return None
        if distance <= 0.0 or distance != distance:
            return None
        return distance


class KinectNextBackend:
    """kinect-next 2.x backend for Kinect for Windows / Xbox One v2 hardware."""

    def __init__(self) -> None:
        try:
            from kinect_next import KinectSensor, StreamType  # type: ignore[import-not-found]
        except ImportError as exc:
            raise KinectV2Unavailable(
                'Kinect v2 support is not installed; install VisionRig with ".[kinect-v2]"'
            ) from exc

        streams = StreamType.COLOR | StreamType.DEPTH | StreamType.INFRARED
        try:
            self._context = KinectSensor(streams=streams)
            self._sensor = self._context.__enter__()
        except Exception as exc:
            raise KinectV2Unavailable(
                "unable to open Kinect v2; verify Kinect SDK 2.0, power/USB adapter "
                "and a dedicated USB 3.0 controller"
            ) from exc
        self._closed = False

    def read(self) -> KinectV2FrameSet | None:
        if self._closed:
            return None
        try:
            frames = self._sensor.wait_for_frames()
        except Exception as exc:
            raise KinectV2ReadError("failed while waiting for Kinect v2 frames") from exc

        color = getattr(frames, "color", None)
        if color is None:
            return None
        image = color.as_bgr().copy()
        height, width = int(image.shape[0]), int(image.shape[1])

        depth = getattr(frames, "depth", None)
        sampler: MetricDepthSampler | None = None
        depth_mm = None
        if depth is not None:
            sampler = _KinectNextDepthSampler(
                self._sensor,
                depth,
                width=width,
                height=height,
            )
            raw_depth = getattr(depth, "data", None)
            if raw_depth is not None:
                depth_mm = raw_depth.copy()

        infrared = None
        ir = getattr(frames, "infrared", None)
        if ir is not None:
            raw_ir = getattr(ir, "data", None)
            infrared = raw_ir.copy() if raw_ir is not None else ir.to_uint8().copy()

        return KinectV2FrameSet(
            color_bgr=image,
            depth_sampler=sampler,
            depth_mm=depth_mm,
            infrared=infrared,
        )

    def close(self) -> None:
        if not self._closed:
            self._context.__exit__(None, None, None)
            self._closed = True


class KinectV2Source:
    """First-class Kinect v2 VisionRig source."""

    def __init__(
        self,
        *,
        source_id: str = "kinect-v2-0",
        backend: KinectV2Backend | None = None,
    ) -> None:
        self.source = SourceDescriptor(
            source_id=source_id,
            source_type="camera",
            device="kinect-v2",
        )
        self._backend = backend or KinectNextBackend()
        self._sequence = 0
        self._closed = False

    def read(self) -> Frame | None:
        if self._closed:
            return None
        frames = self._backend.read()
        if frames is None:
            return None
        sensor_data: dict[str, Any] = {"sensor_model": "kinect-v2"}
        if frames.depth_sampler is not None:
            sensor_data["metric_depth_sampler"] = frames.depth_sampler
        if frames.depth_mm is not None:
            sensor_data["depth_mm"] = frames.depth_mm
        if frames.infrared is not None:
            sensor_data["infrared"] = frames.infrared

        frame = Frame(
            source=self.source,
            sequence=self._sequence,
            payload=frames.color_bgr,
            sensor_data=sensor_data,
        )
        self._sequence += 1
        return frame

    def close(self) -> None:
        if not self._closed:
            self._backend.close()
            self._closed = True


class KinectV2DepthStage:
    """Use Kinect hardware depth through VisionRig's relative-depth v2 surface."""

    name = "kinect_v2_depth"

    def __init__(
        self,
        *,
        min_distance_m: float = 0.5,
        max_distance_m: float = 4.5,
    ) -> None:
        if min_distance_m <= 0.0 or max_distance_m <= min_distance_m:
            raise ValueError("invalid Kinect depth working range")
        self._min = min_distance_m
        self._max = max_distance_m

    def process(self, frame: Frame, current: StageResult) -> StageResult:
        sampler = frame.sensor_data.get("metric_depth_sampler")
        if sampler is None or not current.entities:
            return current

        observations: list[DepthObservation] = []
        for entity in current.entities:
            bbox = entity.bbox
            if bbox is None:
                continue

            x0, y0 = bbox.x, bbox.y
            w, h = bbox.width, bbox.height
            points = (
                (x0 + 0.50 * w, y0 + 0.50 * h),
                (x0 + 0.35 * w, y0 + 0.35 * h),
                (x0 + 0.65 * w, y0 + 0.35 * h),
                (x0 + 0.35 * w, y0 + 0.65 * h),
                (x0 + 0.65 * w, y0 + 0.65 * h),
            )
            distances = [
                value
                for x, y in points
                if (value := sampler.distance_m(float(x), float(y))) is not None
            ]
            if not distances:
                continue

            distance = float(median(distances))
            relative = (distance - self._min) / (self._max - self._min)
            relative = max(0.0, min(1.0, relative))
            observations.append(
                DepthObservation(
                    subject_entity_id=entity.entity_id,
                    relative_depth=relative,
                    distance_m=distance,
                    confidence=len(distances) / len(points),
                    method="kinect-v2-hardware-depth",
                )
            )

        return StageResult(
            entities=current.entities,
            relations=current.relations,
            landmarks=current.landmarks,
            depth=current.depth + tuple(observations),
            scene_label=current.scene_label,
            scene_confidence=current.scene_confidence,
        )
