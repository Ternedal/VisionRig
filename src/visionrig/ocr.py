"""Optional Tesseract OCR stage."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from .contracts import BoundingBox, VisualEntity
from .pipeline import Frame, StageResult


class OcrUnavailable(RuntimeError):
    pass


def _entities_from_tesseract(
    data: dict[str, list[Any]],
    *,
    image_width: int,
    image_height: int,
    min_confidence: float,
) -> tuple[VisualEntity, ...]:
    entities: list[VisualEntity] = []
    count = len(data.get("text", ()))
    for index in range(count):
        text = str(data["text"][index]).strip()
        if not text:
            continue
        try:
            confidence = float(data["conf"][index]) / 100.0
        except (TypeError, ValueError, IndexError):
            continue
        confidence = max(0.0, min(1.0, confidence))
        if confidence < min_confidence:
            continue
        left = max(0, int(data["left"][index]))
        top = max(0, int(data["top"][index]))
        width = max(0, int(data["width"][index]))
        height = max(0, int(data["height"][index]))
        if width == 0 or height == 0:
            continue
        x2 = min(image_width, left + width)
        y2 = min(image_height, top + height)
        if x2 <= left or y2 <= top:
            continue
        entities.append(
            VisualEntity(
                entity_id=f"ocr-{uuid4()}",
                kind="text",
                label=text[:512],
                confidence=float(confidence),
                bbox=BoundingBox(
                    x=float(left / image_width),
                    y=float(top / image_height),
                    width=float((x2 - left) / image_width),
                    height=float((y2 - top) / image_height),
                ),
            )
        )
    return tuple(entities)


class TesseractOcrStage:
    name = "tesseract_ocr"

    def __init__(self, *, min_confidence: float = 0.5, language: str | None = None) -> None:
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1")
        try:
            import pytesseract  # type: ignore[import-not-found]
        except ImportError as exc:
            raise OcrUnavailable(
                'OCR requires VisionRig ".[ocr]" and a local Tesseract executable'
            ) from exc
        self._pytesseract = pytesseract
        self._min_confidence = min_confidence
        self._language = language

    def process(self, frame: Frame, current: StageResult) -> StageResult:
        image = frame.payload
        if not hasattr(image, "shape") or len(image.shape) < 2:
            return current
        height, width = int(image.shape[0]), int(image.shape[1])
        kwargs: dict[str, Any] = {
            "output_type": self._pytesseract.Output.DICT,
        }
        if self._language:
            kwargs["lang"] = self._language
        data = self._pytesseract.image_to_data(image, **kwargs)
        entities = _entities_from_tesseract(
            data,
            image_width=width,
            image_height=height,
            min_confidence=self._min_confidence,
        )
        return StageResult(
            entities=current.entities + entities,
            relations=current.relations,
            landmarks=current.landmarks,
            depth=current.depth,
            scene_label=current.scene_label,
            scene_confidence=current.scene_confidence,
        )
