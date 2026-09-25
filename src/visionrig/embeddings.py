"""Transient visual embeddings.

Vectors are deliberately kept out of PerceptionEvent. They are sidecar features
for future .mrvision recognition/indexing and are bounded in memory.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from math import sqrt
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from .pipeline import Frame, StageResult


@dataclass(frozen=True, slots=True)
class EmbeddingRecord:
    source_id: str
    frame_sequence: int
    subject_entity_id: str | None
    model_id: str
    vector: tuple[float, ...]


class EmbeddingEncoder(Protocol):
    model_id: str

    def encode(self, image: Any) -> tuple[float, ...]: ...


class EmbeddingStore:
    def __init__(self, capacity: int = 256) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._records: OrderedDict[tuple[str, int, str | None, str], EmbeddingRecord] = OrderedDict()
        self._lock = RLock()

    def put(self, record: EmbeddingRecord) -> None:
        key = (
            record.source_id,
            record.frame_sequence,
            record.subject_entity_id,
            record.model_id,
        )
        with self._lock:
            self._records[key] = record
            self._records.move_to_end(key)
            while len(self._records) > self._capacity:
                self._records.popitem(last=False)

    def get(
        self,
        source_id: str,
        frame_sequence: int,
        *,
        subject_entity_id: str | None = None,
        model_id: str,
    ) -> EmbeddingRecord | None:
        key = (source_id, frame_sequence, subject_entity_id, model_id)
        with self._lock:
            return self._records.get(key)

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)


class EmbeddingStage:
    name = "visual_embeddings"

    def __init__(self, encoder: EmbeddingEncoder, store: EmbeddingStore) -> None:
        self._encoder = encoder
        self._store = store

    def process(self, frame: Frame, current: StageResult) -> StageResult:
        vector = self._encoder.encode(frame.payload)
        self._store.put(
            EmbeddingRecord(
                source_id=frame.source.source_id,
                frame_sequence=frame.sequence,
                subject_entity_id=None,
                model_id=self._encoder.model_id,
                vector=vector,
            )
        )
        return current


class OnnxImageEmbeddingEncoder:
    """Generic single-input/single-output ONNX image encoder."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        model_id: str,
        input_width: int = 224,
        input_height: int = 224,
        mean: tuple[float, float, float] = (0.48145466, 0.4578275, 0.40821073),
        std: tuple[float, float, float] = (0.26862954, 0.26130258, 0.27577711),
        prefer_cuda: bool = True,
    ) -> None:
        try:
            import cv2  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]
            import onnxruntime as ort  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                'embedding inference requires VisionRig ".[inference]"'
            ) from exc
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        providers = ["CPUExecutionProvider"]
        available = set(ort.get_available_providers())
        if prefer_cuda and "CUDAExecutionProvider" in available:
            providers.insert(0, "CUDAExecutionProvider")
        self._cv2 = cv2
        self._np = np
        self._session = ort.InferenceSession(str(path), providers=providers)
        inputs = self._session.get_inputs()
        if len(inputs) != 1:
            raise RuntimeError("embedding adapter requires exactly one model input")
        self._input_name = inputs[0].name
        self._width = input_width
        self._height = input_height
        self._mean = np.asarray(mean, dtype=np.float32).reshape(1, 1, 3)
        self._std = np.asarray(std, dtype=np.float32).reshape(1, 1, 3)
        self.model_id = model_id
        self.providers = tuple(self._session.get_providers())

    def encode(self, image: Any) -> tuple[float, ...]:
        np = self._np
        rgb = self._cv2.cvtColor(image, self._cv2.COLOR_BGR2RGB)
        resized = self._cv2.resize(rgb, (self._width, self._height)).astype(np.float32) / 255.0
        normalized = (resized - self._mean) / self._std
        tensor = normalized.transpose(2, 0, 1)[None, ...].astype(np.float32)
        outputs = self._session.run(None, {self._input_name: tensor})
        if not outputs:
            raise RuntimeError("embedding model returned no output")
        vector = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if norm <= 0.0:
            raise RuntimeError("embedding model returned zero vector")
        vector = vector / norm
        return tuple(float(value) for value in vector)
