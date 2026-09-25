"""VisionRig core package."""

from .contracts import (
    DepthObservation,
    LandmarkObservation,
    PerceptionEvent,
    VisualEntity,
    VisualLandmark,
    VisualRelation,
)
from .pipeline import PerceptionPipeline

__all__ = [
    "DepthObservation",
    "LandmarkObservation",
    "PerceptionEvent",
    "VisualEntity",
    "VisualLandmark",
    "VisualRelation",
    "PerceptionPipeline",
]

__version__ = "0.9.0"
