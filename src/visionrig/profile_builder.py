"""Operator-driven .mrvision enrollment from curated image samples."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .embeddings import EmbeddingEncoder, OnnxImageEmbeddingEncoder
from .model_manifest import EmbeddingModelManifest, load_verified_embedding_model
from .profile import EnrollmentKind, MrVisionError, MrVisionProfile, enroll_embedding


ImageLoader = Callable[[Path], Any]


def build_embedding_encoder(
    manifest_path: str | Path,
    *,
    prefer_cuda: bool = True,
) -> OnnxImageEmbeddingEncoder:
    verified = load_verified_embedding_model(manifest_path)
    manifest = verified.manifest
    assert isinstance(manifest, EmbeddingModelManifest)
    return OnnxImageEmbeddingEncoder(
        verified.artifact_path,
        model_id=manifest.model_id,
        input_width=manifest.input_width,
        input_height=manifest.input_height,
        mean=manifest.mean,
        std=manifest.std,
        prefer_cuda=prefer_cuda,
    )


def _read_image(path: Path) -> Any:
    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError as exc:
        raise MrVisionError(
            'image enrollment requires VisionRig ".[inference]"'
        ) from exc
    image = cv2.imread(str(path))
    if image is None:
        raise MrVisionError(f"unable to decode enrollment image: {path}")
    return image


def _file_source_ref(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise MrVisionError(f"unable to read enrollment image: {path}") from exc
    return "image-sha256:" + digest.hexdigest()


def _crop_image(
    image: Any,
    crop: tuple[float, float, float, float] | None,
) -> Any:
    if crop is None:
        return image
    if not hasattr(image, "shape") or len(image.shape) < 2:
        raise MrVisionError("enrollment crop requires an image array")
    x, y, width, height = crop
    if (
        x < 0.0
        or y < 0.0
        or width <= 0.0
        or height <= 0.0
        or x + width > 1.0
        or y + height > 1.0
    ):
        raise MrVisionError(
            "crop must be normalized x y width height inside 0..1"
        )
    image_height, image_width = int(image.shape[0]), int(image.shape[1])
    x1 = max(0, min(image_width - 1, int(x * image_width)))
    y1 = max(0, min(image_height - 1, int(y * image_height)))
    x2 = max(x1 + 1, min(image_width, int((x + width) * image_width)))
    y2 = max(y1 + 1, min(image_height, int((y + height) * image_height)))
    cropped = image[y1:y2, x1:x2]
    if getattr(cropped, "size", 0) == 0:
        raise MrVisionError("enrollment crop is empty")
    return cropped


def enroll_image_files(
    profile: MrVisionProfile,
    *,
    image_paths: Sequence[str | Path],
    encoder: EmbeddingEncoder,
    kind: EnrollmentKind,
    label: str,
    quality: float = 0.85,
    subject_ref: str | None = None,
    crop: tuple[float, float, float, float] | None = None,
    image_loader: ImageLoader = _read_image,
    now: datetime | None = None,
) -> MrVisionProfile:
    if not isinstance(profile, MrVisionProfile):
        raise TypeError("profile must be MrVisionProfile")
    if not 0.0 <= quality <= 1.0:
        raise MrVisionError("quality must be between 0 and 1")
    if not label.strip():
        raise MrVisionError("label must not be empty")
    paths = [Path(item).resolve() for item in image_paths]
    if not paths:
        raise MrVisionError("at least one enrollment image is required")
    if len(paths) > 128:
        raise MrVisionError("at most 128 images may be enrolled per operation")

    updated = profile
    for path in paths:
        if not path.is_file():
            raise MrVisionError(f"enrollment image does not exist: {path}")
        image = image_loader(path)
        sample = _crop_image(image, crop)
        vector = encoder.encode(sample)
        updated = enroll_embedding(
            updated,
            kind=kind,
            label=label.strip(),
            subject_ref=subject_ref,
            vector=vector,
            embedding_model_id=encoder.model_id,
            quality=quality,
            source_ref=_file_source_ref(path),
            now=now,
        )
    return updated
