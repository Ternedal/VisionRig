"""Runtime capability probing without making optional stacks mandatory."""
from __future__ import annotations

from importlib.util import find_spec
from shutil import which
from typing import Any


def _module_available(name: str) -> bool:
    try:
        return find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def probe_capabilities() -> dict[str, Any]:
    opencv = _module_available("cv2")
    onnxruntime = _module_available("onnxruntime")
    providers: list[str] = []
    if onnxruntime:
        try:
            import onnxruntime as ort  # type: ignore[import-not-found]
            providers = list(ort.get_available_providers())
        except Exception:
            providers = []

    return {
        "schema": "visionrig/capabilities/v2",
        "capture": {
            "opencv": opencv,
            "camera": opencv,
            "image": opencv,
            "video": opencv,
            "kinect_v2": _module_available("kinect_next"),
        },
        "perception": {
            "tesseract_python": _module_available("pytesseract"),
            "tesseract_binary": which("tesseract") is not None,
            "mediapipe": _module_available("mediapipe"),
        },
        "inference": {
            "onnxruntime": onnxruntime,
            "providers": providers,
            "cuda": "CUDAExecutionProvider" in providers,
        },
    }
