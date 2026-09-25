"""VisionRig core package."""

from .contracts import PerceptionEvent, VisualEntity, VisualRelation
from .pipeline import PerceptionPipeline

__all__ = [
    "PerceptionEvent",
    "VisualEntity",
    "VisualRelation",
    "PerceptionPipeline",
]

__version__ = "0.1.0"
