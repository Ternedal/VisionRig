"""Factories binding verified optional model artifacts into pipelines."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .depth_onnx import OnnxDepthStage
from .embeddings import (
    EmbeddingStage,
    EmbeddingStore,
    EntityEmbeddingStage,
    OnnxImageEmbeddingEncoder,
)
from .landmarks_mediapipe import MediaPipeLandmarkStage
from .model_manifest import (
    DepthModelManifest,
    EmbeddingModelManifest,
    YoloModelManifest,
    load_verified_depth_model,
    load_verified_embedding_model,
    load_verified_yolo_model,
)
from .ocr import TesseractOcrStage
from .pipeline import PerceptionPipeline, Stage
from .profile import MrVisionProfile
from .recognition import ProfileRecognitionStage
from .spatial import SpatialRelationStage
from .tracking import IoUTrackingStage
from .yolo_onnx import YoloOnnxStage


@dataclass(frozen=True, slots=True)
class PipelineBundle:
    pipeline: PerceptionPipeline
    embedding_store: EmbeddingStore | None = None


def build_yolo_pipeline(
    manifest_path: str | Path,
    *,
    prefer_cuda: bool = True,
    spatial_relations: bool = True,
) -> PerceptionPipeline:
    verified = load_verified_yolo_model(manifest_path)
    manifest = verified.manifest
    assert isinstance(manifest, YoloModelManifest)
    stages: list[Stage] = [
        YoloOnnxStage(
            verified.artifact_path,
            manifest.labels,
            label_kinds=manifest.label_kinds,
            input_size=manifest.input_size,
            confidence_threshold=manifest.confidence_threshold,
            iou_threshold=manifest.iou_threshold,
            prefer_cuda=prefer_cuda,
        ),
        IoUTrackingStage(),
    ]
    if spatial_relations:
        stages.append(SpatialRelationStage())
    return PerceptionPipeline(tuple(stages))


def build_pipeline(
    *,
    yolo_manifest: str | Path | None = None,
    depth_manifest: str | Path | None = None,
    embedding_manifest: str | Path | None = None,
    profile: MrVisionProfile | None = None,
    recognition_threshold: float = 0.75,
    ocr: bool = False,
    landmarks: bool = False,
    spatial_relations: bool = True,
    prefer_cuda: bool = True,
) -> PipelineBundle:
    stages: list[Stage] = []
    store: EmbeddingStore | None = None

    if yolo_manifest is not None:
        verified = load_verified_yolo_model(yolo_manifest)
        manifest = verified.manifest
        assert isinstance(manifest, YoloModelManifest)
        stages.append(
            YoloOnnxStage(
                verified.artifact_path,
                manifest.labels,
                label_kinds=manifest.label_kinds,
                input_size=manifest.input_size,
                confidence_threshold=manifest.confidence_threshold,
                iou_threshold=manifest.iou_threshold,
                prefer_cuda=prefer_cuda,
            )
        )
        stages.append(IoUTrackingStage())

    if ocr:
        stages.append(TesseractOcrStage())
    if landmarks:
        stages.append(MediaPipeLandmarkStage())

    if depth_manifest is not None:
        verified = load_verified_depth_model(depth_manifest)
        manifest = verified.manifest
        assert isinstance(manifest, DepthModelManifest)
        stages.append(
            OnnxDepthStage(
                verified.artifact_path,
                input_width=manifest.input_width,
                input_height=manifest.input_height,
                mean=manifest.mean,
                std=manifest.std,
                higher_is_nearer=manifest.higher_is_nearer,
                prefer_cuda=prefer_cuda,
            )
        )

    if profile is not None and embedding_manifest is None:
        raise ValueError("profile recognition requires an embedding_manifest")

    if embedding_manifest is not None:
        verified = load_verified_embedding_model(embedding_manifest)
        manifest = verified.manifest
        assert isinstance(manifest, EmbeddingModelManifest)
        store = EmbeddingStore()
        encoder = OnnxImageEmbeddingEncoder(
            verified.artifact_path,
            model_id=manifest.model_id,
            input_width=manifest.input_width,
            input_height=manifest.input_height,
            mean=manifest.mean,
            std=manifest.std,
            prefer_cuda=prefer_cuda,
        )
        stages.append(EmbeddingStage(encoder, store))
        if profile is not None:
            stages.append(EntityEmbeddingStage(encoder, store))
            stages.append(
                ProfileRecognitionStage(
                    profile,
                    store,
                    embedding_model_id=manifest.model_id,
                    threshold=recognition_threshold,
                )
            )

    if spatial_relations:
        stages.append(SpatialRelationStage())

    return PipelineBundle(
        pipeline=PerceptionPipeline(tuple(stages)),
        embedding_store=store,
    )
