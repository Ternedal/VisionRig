"""VisionRig core package."""

from .contracts import (
    DepthObservation,
    InfraredObservation,
    LandmarkObservation,
    PerceptionEvent,
    VisualEntity,
    VisualLandmark,
    VisualRelation,
)
from .pipeline import PerceptionPipeline

__all__ = [
    "DepthObservation",
    "InfraredObservation",
    "LandmarkObservation",
    "PerceptionEvent",
    "VisualEntity",
    "VisualLandmark",
    "VisualRelation",
    "PerceptionPipeline",
]

__version__ = "0.91.0"
