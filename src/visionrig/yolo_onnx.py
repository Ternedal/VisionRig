"""Optional YOLOv8-style ONNX object detector stage."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .contracts import BoundingBox, EntityKind, VisualEntity
from .pipeline import Frame, StageResult


class InferenceUnavailable(RuntimeError):
    pass


class InferenceContractError(RuntimeError):
    pass


def entity_kind_for_label(
    label: str,
    label_kinds: Mapping[str, EntityKind] | None = None,
) -> EntityKind:
    if label_kinds and label in label_kinds:
        return label_kinds[label]
    if label == "person":
        return "person"
    return "object"


class YoloOnnxStage:
    name = "yolo_onnx"

    def __init__(
        self,
        model_path: str | Path,
        labels: tuple[str, ...],
        *,
        label_kinds: Mapping[str, EntityKind] | None = None,
        input_size: int = 640,
        confidence_threshold: float = 0.25,
        iou_threshold: float = 0.45,
        prefer_cuda: bool = True,
    ) -> None:
        if not labels:
            raise ValueError("labels must not be empty")
        unknown_kind_labels = set(label_kinds or {}) - set(labels)
        if unknown_kind_labels:
            raise ValueError("label_kinds contains labels absent from labels")
        if input_size < 32:
            raise ValueError("input_size must be >= 32")
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1")
        if not 0.0 <= iou_threshold <= 1.0:
            raise ValueError("iou_threshold must be between 0 and 1")
        try:
            import cv2  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]
            import onnxruntime as ort  # type: ignore[import-not-found]
        except ImportError as exc:
            raise InferenceUnavailable(
                'YOLO ONNX support requires VisionRig ".[inference]" dependencies'
            ) from exc

        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        available = set(ort.get_available_providers())
        providers = ["CPUExecutionProvider"]
        if prefer_cuda and "CUDAExecutionProvider" in available:
            providers.insert(0, "CUDAExecutionProvider")

        self._cv2 = cv2
        self._np = np
        self._session = ort.InferenceSession(str(path), providers=providers)
        inputs = self._session.get_inputs()
        if len(inputs) != 1:
            raise InferenceContractError("YOLO adapter requires exactly one model input")
        self._input_name = inputs[0].name
        self._labels = labels
        self._label_kinds = dict(label_kinds or {})
        self._size = input_size
        self._confidence = confidence_threshold
        self._iou = iou_threshold
        self.providers = tuple(self._session.get_providers())

    def _preprocess(self, image: Any) -> tuple[Any, float, int, int, int, int]:
        np = self._np
        cv2 = self._cv2
        if not hasattr(image, "shape") or len(image.shape) < 2:
            raise InferenceContractError("frame payload is not an image array")
        height, width = int(image.shape[0]), int(image.shape[1])
        if height < 1 or width < 1:
            raise InferenceContractError("image dimensions must be positive")

        scale = min(self._size / width, self._size / height)
        resized_w = max(1, round(width * scale))
        resized_h = max(1, round(height * scale))
        resized = cv2.resize(image, (resized_w, resized_h))
        pad_x = (self._size - resized_w) // 2
        pad_y = (self._size - resized_h) // 2
        canvas = np.full((self._size, self._size, 3), 114, dtype=np.uint8)
        canvas[pad_y:pad_y + resized_h, pad_x:pad_x + resized_w] = resized
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        tensor = rgb.transpose(2, 0, 1).astype(np.float32) / 255.0
        tensor = np.expand_dims(np.ascontiguousarray(tensor), axis=0)
        return tensor, scale, pad_x, pad_y, width, height

    def process(self, frame: Frame, current: StageResult) -> StageResult:
        tensor, scale, pad_x, pad_y, width, height = self._preprocess(frame.payload)
        outputs = self._session.run(None, {self._input_name: tensor})
        if not outputs:
            raise InferenceContractError("model returned no outputs")

        rows = self._np.asarray(outputs[0])
        if rows.ndim == 3:
            if rows.shape[0] != 1:
                raise InferenceContractError("unexpected YOLO batch dimension")
            rows = rows[0]
        if rows.ndim != 2:
            raise InferenceContractError("unexpected YOLO output rank")
        if rows.shape[0] < rows.shape[1]:
            rows = rows.T

        expected = 4 + len(self._labels)
        if rows.shape[1] < expected:
            raise InferenceContractError(
                f"YOLO output has {rows.shape[1]} columns; expected at least {expected}"
            )

        boxes: list[list[int]] = []
        scores: list[float] = []
        class_ids: list[int] = []
        normalized: list[BoundingBox] = []

        for row in rows:
            class_scores = row[4:4 + len(self._labels)]
            class_id = int(self._np.argmax(class_scores))
            confidence = float(class_scores[class_id])
            if confidence < self._confidence:
                continue
            cx, cy, w, h = (float(v) for v in row[:4])
            x1 = (cx - w / 2.0 - pad_x) / scale
            y1 = (cy - h / 2.0 - pad_y) / scale
            x2 = (cx + w / 2.0 - pad_x) / scale
            y2 = (cy + h / 2.0 - pad_y) / scale
            x1, y1 = max(0.0, x1), max(0.0, y1)
            x2, y2 = min(float(width), x2), min(float(height), y2)
            if x2 <= x1 or y2 <= y1:
                continue
            boxes.append([round(x1), round(y1), round(x2 - x1), round(y2 - y1)])
            scores.append(confidence)
            class_ids.append(class_id)
            normalized.append(
                BoundingBox(
                    x=float(x1 / width),
                    y=float(y1 / height),
                    width=float((x2 - x1) / width),
                    height=float((y2 - y1) / height),
                )
            )

        if not boxes:
            return current
        selected = self._cv2.dnn.NMSBoxes(boxes, scores, self._confidence, self._iou)
        indices = [int(i) for i in self._np.asarray(selected).reshape(-1)]
        detections = tuple(
            VisualEntity(
                entity_id=f"det-{uuid4()}",
                kind=entity_kind_for_label(
                    self._labels[class_ids[i]],
                    self._label_kinds,
                ),
                label=self._labels[class_ids[i]],
                confidence=float(scores[i]),
                bbox=normalized[i],
            )
            for i in indices
        )
        return StageResult(
            entities=current.entities + detections,
            relations=current.relations,
            landmarks=current.landmarks,
            depth=current.depth,
            scene_label=current.scene_label,
            scene_confidence=current.scene_confidence,
        )
