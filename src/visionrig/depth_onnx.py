"""Optional generic ONNX monocular-depth stage."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import DepthObservation
from .pipeline import Frame, StageResult


class DepthUnavailable(RuntimeError):
    pass


class DepthContractError(RuntimeError):
    pass


class OnnxDepthStage:
    name = "onnx_depth"

    def __init__(
        self,
        model_path: str | Path,
        *,
        input_width: int = 384,
        input_height: int = 384,
        mean: tuple[float, float, float] = (0.485, 0.456, 0.406),
        std: tuple[float, float, float] = (0.229, 0.224, 0.225),
        higher_is_nearer: bool = True,
        prefer_cuda: bool = True,
    ) -> None:
        try:
            import cv2  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]
            import onnxruntime as ort  # type: ignore[import-not-found]
        except ImportError as exc:
            raise DepthUnavailable(
                'depth requires VisionRig ".[inference]" dependencies'
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
            raise DepthContractError("depth adapter requires exactly one model input")
        self._input_name = inputs[0].name
        self._width = input_width
        self._height = input_height
        self._mean = np.asarray(mean, dtype=np.float32).reshape(1, 1, 3)
        self._std = np.asarray(std, dtype=np.float32).reshape(1, 1, 3)
        self._higher_is_nearer = higher_is_nearer
        self.providers = tuple(self._session.get_providers())

    def _depth_map(self, image: Any) -> Any:
        np = self._np
        cv2 = self._cv2
        if not hasattr(image, "shape") or len(image.shape) < 2:
            raise DepthContractError("frame payload is not an image array")
        original_h, original_w = int(image.shape[0]), int(image.shape[1])
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (self._width, self._height)).astype(np.float32) / 255.0
        normalized = (resized - self._mean) / self._std
        tensor = normalized.transpose(2, 0, 1)[None, ...].astype(np.float32)
        outputs = self._session.run(None, {self._input_name: tensor})
        if not outputs:
            raise DepthContractError("depth model returned no output")
        depth = np.asarray(outputs[0]).squeeze()
        if depth.ndim != 2:
            raise DepthContractError("depth model output must reduce to HxW")
        depth = cv2.resize(depth.astype(np.float32), (original_w, original_h))
        finite = np.isfinite(depth)
        if not finite.any():
            raise DepthContractError("depth model returned no finite values")
        low = float(depth[finite].min())
        high = float(depth[finite].max())
        if high <= low:
            relative = np.zeros_like(depth, dtype=np.float32)
        else:
            relative = (depth - low) / (high - low)
        if self._higher_is_nearer:
            relative = 1.0 - relative
        return np.clip(relative, 0.0, 1.0)

    def process(self, frame: Frame, current: StageResult) -> StageResult:
        if not current.entities:
            return current
        depth_map = self._depth_map(frame.payload)
        height, width = depth_map.shape
        observations: list[DepthObservation] = []
        for entity in current.entities:
            if entity.bbox is None:
                continue
            x1 = max(0, min(width - 1, int(entity.bbox.x * width)))
            y1 = max(0, min(height - 1, int(entity.bbox.y * height)))
            x2 = max(x1 + 1, min(width, int((entity.bbox.x + entity.bbox.width) * width)))
            y2 = max(y1 + 1, min(height, int((entity.bbox.y + entity.bbox.height) * height)))
            region = depth_map[y1:y2, x1:x2]
            if region.size == 0:
                continue
            value = float(self._np.median(region))
            observations.append(
                DepthObservation(
                    subject_entity_id=entity.entity_id,
                    relative_depth=max(0.0, min(1.0, value)),
                    confidence=None,
                    method="onnx-monocular-relative",
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
