"""Environment composition for a real VisionRig service process."""
from __future__ import annotations

from dataclasses import dataclass
import os

from .pipeline_factory import PipelineBundle, build_pipeline


class ServiceConfigError(RuntimeError):
    pass


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ServiceConfigError(
        f"{name} must be one of 1/0, true/false, yes/no or on/off"
    )


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    yolo_manifest: str | None = None
    depth_manifest: str | None = None
    embedding_manifest: str | None = None
    ocr: bool = False
    landmarks: bool = False
    spatial_relations: bool = True
    prefer_cuda: bool = True
    max_sensor_frame_bytes: int = 8 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        raw_limit = os.getenv("VISIONRIG_MAX_SENSOR_FRAME_BYTES")
        try:
            limit = int(raw_limit) if raw_limit is not None else 8 * 1024 * 1024
        except ValueError as exc:
            raise ServiceConfigError(
                "VISIONRIG_MAX_SENSOR_FRAME_BYTES must be an integer"
            ) from exc
        if limit < 1024 or limit > 64 * 1024 * 1024:
            raise ServiceConfigError(
                "VISIONRIG_MAX_SENSOR_FRAME_BYTES must be between 1024 and 67108864"
            )

        return cls(
            yolo_manifest=os.getenv("VISIONRIG_YOLO_MANIFEST"),
            depth_manifest=os.getenv("VISIONRIG_DEPTH_MANIFEST"),
            embedding_manifest=os.getenv("VISIONRIG_EMBEDDING_MANIFEST"),
            ocr=_flag("VISIONRIG_OCR"),
            landmarks=_flag("VISIONRIG_LANDMARKS"),
            spatial_relations=_flag("VISIONRIG_SPATIAL_RELATIONS", True),
            prefer_cuda=not _flag("VISIONRIG_FORCE_CPU"),
            max_sensor_frame_bytes=limit,
        )

    def build_bundle(self) -> PipelineBundle:
        return build_pipeline(
            yolo_manifest=self.yolo_manifest,
            depth_manifest=self.depth_manifest,
            embedding_manifest=self.embedding_manifest,
            ocr=self.ocr,
            landmarks=self.landmarks,
            spatial_relations=self.spatial_relations,
            prefer_cuda=self.prefer_cuda,
        )
