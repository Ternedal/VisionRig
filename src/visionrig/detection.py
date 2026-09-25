"""Detector contracts shared by inference adapters."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .contracts import BoundingBox
from .pipeline import Frame


@dataclass(frozen=True, slots=True)
class Detection:
    label: str
    confidence: float
    bbox: BoundingBox


class Detector(Protocol):
    name: str

    def detect(self, frame: Frame) -> tuple[Detection, ...]: ...
