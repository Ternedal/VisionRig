"""Factories that bind verified optional model artifacts into a pipeline."""
from __future__ import annotations

from pathlib import Path

from .model_manifest import load_verified_yolo_model
from .pipeline import PerceptionPipeline
from .tracking import IoUTrackingStage
from .yolo_onnx import YoloOnnxStage


def build_yolo_pipeline(
    manifest_path: str | Path,
    *,
    prefer_cuda: bool = True,
) -> PerceptionPipeline:
    verified = load_verified_yolo_model(manifest_path)
    manifest = verified.manifest
    detector = YoloOnnxStage(
        verified.artifact_path,
        manifest.labels,
        input_size=manifest.input_size,
        confidence_threshold=manifest.confidence_threshold,
        iou_threshold=manifest.iou_threshold,
        prefer_cuda=prefer_cuda,
    )
    tracker = IoUTrackingStage()
    return PerceptionPipeline((detector, tracker))
