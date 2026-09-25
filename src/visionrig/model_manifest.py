"""Verified model artifact manifests for optional perception adapters."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import EntityKind


class ModelManifestError(RuntimeError):
    pass


class _ArtifactManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: str = Field(min_length=1, max_length=2048)
    artifact_license: str = Field(min_length=1, max_length=256)


class YoloModelManifest(_ArtifactManifest):
    schema_id: Literal["visionrig/yolo-model-manifest/v1"] = "visionrig/yolo-model-manifest/v1"
    labels: tuple[str, ...] = Field(min_length=1)
    label_kinds: dict[str, EntityKind] = Field(default_factory=dict)
    input_size: int = Field(default=640, ge=32, le=4096)
    confidence_threshold: float = Field(default=0.25, ge=0.0, le=1.0)
    iou_threshold: float = Field(default=0.45, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_label_kinds(self) -> "YoloModelManifest":
        unknown = sorted(set(self.label_kinds) - set(self.labels))
        if unknown:
            raise ValueError(
                "label_kinds contains labels absent from labels: " + ", ".join(unknown)
            )
        return self


class DepthModelManifest(_ArtifactManifest):
    schema_id: Literal["visionrig/depth-model-manifest/v1"] = "visionrig/depth-model-manifest/v1"
    input_width: int = Field(default=384, ge=32, le=4096)
    input_height: int = Field(default=384, ge=32, le=4096)
    mean: tuple[float, float, float] = (0.485, 0.456, 0.406)
    std: tuple[float, float, float] = (0.229, 0.224, 0.225)
    higher_is_nearer: bool = True


class EmbeddingModelManifest(_ArtifactManifest):
    schema_id: Literal["visionrig/embedding-model-manifest/v1"] = "visionrig/embedding-model-manifest/v1"
    model_id: str = Field(min_length=1, max_length=256)
    input_width: int = Field(default=224, ge=32, le=4096)
    input_height: int = Field(default=224, ge=32, le=4096)
    mean: tuple[float, float, float] = (0.48145466, 0.4578275, 0.40821073)
    std: tuple[float, float, float] = (0.26862954, 0.26130258, 0.27577711)


ManifestT = TypeVar("ManifestT", bound=_ArtifactManifest)


@dataclass(frozen=True, slots=True)
class VerifiedModel:
    manifest: _ArtifactManifest
    manifest_path: Path
    artifact_path: Path


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_verified(path: str | Path, model_type: type[ManifestT]) -> tuple[ManifestT, Path, Path]:
    manifest_path = Path(path).resolve()
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = model_type.model_validate(raw)
    except Exception as exc:
        raise ModelManifestError(f"invalid model manifest: {manifest_path}") from exc

    model_root = manifest_path.parent.resolve()
    artifact_path = (model_root / manifest.artifact).resolve()
    if not artifact_path.is_relative_to(model_root):
        raise ModelManifestError("model artifact must stay inside the manifest directory")
    if not artifact_path.is_file():
        raise ModelManifestError(f"model artifact does not exist: {artifact_path}")

    actual = _sha256_file(artifact_path)
    if actual != manifest.sha256:
        raise ModelManifestError(
            f"model checksum mismatch: expected {manifest.sha256}, got {actual}"
        )
    return manifest, manifest_path, artifact_path


def load_verified_yolo_model(path: str | Path) -> VerifiedModel:
    manifest, manifest_path, artifact_path = _load_verified(path, YoloModelManifest)
    return VerifiedModel(manifest, manifest_path, artifact_path)


def load_verified_depth_model(path: str | Path) -> VerifiedModel:
    manifest, manifest_path, artifact_path = _load_verified(path, DepthModelManifest)
    return VerifiedModel(manifest, manifest_path, artifact_path)


def load_verified_embedding_model(path: str | Path) -> VerifiedModel:
    manifest, manifest_path, artifact_path = _load_verified(path, EmbeddingModelManifest)
    return VerifiedModel(manifest, manifest_path, artifact_path)
