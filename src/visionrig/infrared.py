"""Bounded infrared frame summary stage.

Raw infrared planes remain frame-local. This stage emits only normalized,
bounded statistics suitable for PerceptionEvent/v4.
"""
from __future__ import annotations

from typing import Any

from .contracts import InfraredObservation
from .pipeline import Frame, StageResult


class InfraredSummaryStage:
    name = "infrared_summary"

    def __init__(self, *, hotspot_threshold: float = 0.75) -> None:
        if not 0.0 < hotspot_threshold < 1.0:
            raise ValueError("hotspot_threshold must be between 0 and 1")
        self._hotspot_threshold = float(hotspot_threshold)

    @staticmethod
    def _normalized_plane(value: Any):
        try:
            import numpy as np  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("infrared summary requires numpy") from exc

        plane = np.asarray(value)
        if plane.size == 0 or plane.ndim not in {1, 2}:
            return None
        if not np.issubdtype(plane.dtype, np.number):
            return None

        numeric = plane.astype(np.float64, copy=False)
        finite = np.isfinite(numeric)
        if not finite.any():
            return None
        numeric = numeric[finite]

        if np.issubdtype(plane.dtype, np.integer):
            info = np.iinfo(plane.dtype)
            maximum = float(info.max)
        else:
            maximum = float(numeric.max())
            if maximum <= 1.0:
                maximum = 1.0

        if maximum <= 0.0:
            return None
        return np.clip(numeric / maximum, 0.0, 1.0)

    def process(self, frame: Frame, current: StageResult) -> StageResult:
        raw = frame.sensor_data.get("infrared")
        if raw is None:
            return current

        normalized = self._normalized_plane(raw)
        if normalized is None:
            return current

        import numpy as np  # type: ignore[import-not-found]

        mean_intensity = float(np.mean(normalized))
        contrast = float(np.std(normalized))
        hotspot_fraction = float(np.mean(normalized >= self._hotspot_threshold))
        observation = InfraredObservation(
            mean_intensity=max(0.0, min(1.0, mean_intensity)),
            contrast=max(0.0, min(1.0, contrast)),
            hotspot_fraction=max(0.0, min(1.0, hotspot_fraction)),
            sample_count=int(normalized.size),
        )
        return StageResult(
            entities=current.entities,
            relations=current.relations,
            landmarks=current.landmarks,
            depth=current.depth,
            infrared=current.infrared + (observation,),
            scene_label=current.scene_label,
            scene_confidence=current.scene_confidence,
        )
