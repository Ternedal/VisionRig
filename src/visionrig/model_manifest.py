"""Verified model artifact manifests for optional perception adapters."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelManifestError(RuntimeError):
    pass


class YoloModelManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_id: Literal["visionrig/yolo-model-manifest/v1"] = (
        "visionrig/yolo-model-manifest/v1"
    )
    artifact: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: str = Field(min_length=1, max_length=2048)
    artifact_license: str = Field(min_length=1, max_length=256)
    labels: tuple[str, ...] = Field(min_length=1)
    input_size: int = Field(default=640, ge=32, le=4096)
    confidence_threshold: float = Field(default=0.25, ge=0.0, le=1.0)
    iou_threshold: float = Field(default=0.45, ge=0.0, le=1.0)


@dataclass(frozen=True, slots=True)
class VerifiedYoloModel:
    manifest: YoloModelManifest
    manifest_path: Path
    artifact_path: Path


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_verified_yolo_model(path: str | Path) -> VerifiedYoloModel:
    manifest_path = Path(path).resolve()
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = YoloModelManifest.model_validate(raw)
    except Exception as exc:
        raise ModelManifestError(f"invalid model manifest: {manifest_path}") from exc

    artifact_path = (manifest_path.parent / manifest.artifact).resolve()
    if not artifact_path.is_file():
        raise ModelManifestError(f"model artifact does not exist: {artifact_path}")

    actual = _sha256_file(artifact_path)
    if actual != manifest.sha256:
        raise ModelManifestError(
            f"model checksum mismatch: expected {manifest.sha256}, got {actual}"
        )

    return VerifiedYoloModel(
        manifest=manifest,
        manifest_path=manifest_path,
        artifact_path=artifact_path,
    )
