"""Environment composition for a real VisionRig service process."""
from __future__ import annotations

from dataclasses import dataclass
import os

from .pipeline_factory import PipelineBundle, build_pipeline
from .profile import MrVisionProfile
from .profile_io import load_encrypted_profile


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


def _threshold(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ServiceConfigError(f"{name} must be a number") from exc
    if not 0.0 <= value <= 1.0:
        raise ServiceConfigError(f"{name} must be between 0 and 1")
    return value


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    yolo_manifest: str | None = None
    depth_manifest: str | None = None
    embedding_manifest: str | None = None
    mrvision_profile: str | None = None
    mrvision_key_file: str | None = None
    recognition_threshold: float = 0.75
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

        profile = os.getenv("VISIONRIG_MRVISION_PROFILE")
        key_file = os.getenv("VISIONRIG_MRVISION_KEY_FILE")
        if bool(profile) != bool(key_file):
            raise ServiceConfigError(
                "VISIONRIG_MRVISION_PROFILE and VISIONRIG_MRVISION_KEY_FILE "
                "must be configured together"
            )

        embedding = os.getenv("VISIONRIG_EMBEDDING_MANIFEST")
        if profile and not embedding:
            raise ServiceConfigError(
                "VISIONRIG_MRVISION_PROFILE requires VISIONRIG_EMBEDDING_MANIFEST"
            )

        return cls(
            yolo_manifest=os.getenv("VISIONRIG_YOLO_MANIFEST"),
            depth_manifest=os.getenv("VISIONRIG_DEPTH_MANIFEST"),
            embedding_manifest=embedding,
            mrvision_profile=profile,
            mrvision_key_file=key_file,
            recognition_threshold=_threshold(
                "VISIONRIG_RECOGNITION_THRESHOLD",
                0.75,
            ),
            ocr=_flag("VISIONRIG_OCR"),
            landmarks=_flag("VISIONRIG_LANDMARKS"),
            spatial_relations=_flag("VISIONRIG_SPATIAL_RELATIONS", True),
            prefer_cuda=not _flag("VISIONRIG_FORCE_CPU"),
            max_sensor_frame_bytes=limit,
        )

    def load_profile(self) -> MrVisionProfile | None:
        if self.mrvision_profile is None:
            return None
        assert self.mrvision_key_file is not None
        return load_encrypted_profile(
            self.mrvision_profile,
            self.mrvision_key_file,
        )

    def build_bundle(self) -> PipelineBundle:
        return build_pipeline(
            yolo_manifest=self.yolo_manifest,
            depth_manifest=self.depth_manifest,
            embedding_manifest=self.embedding_manifest,
            profile=self.load_profile(),
            recognition_threshold=self.recognition_threshold,
            ocr=self.ocr,
            landmarks=self.landmarks,
            spatial_relations=self.spatial_relations,
            prefer_cuda=self.prefer_cuda,
        )
