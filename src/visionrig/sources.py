"""Optional OpenCV-backed frame sources.

OpenCV is imported lazily so importing VisionRig core never requires camera
dependencies.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from .contracts import SourceDescriptor
from .pipeline import Frame


class CaptureUnavailable(RuntimeError):
    pass


class CaptureReadError(RuntimeError):
    pass


class FrameSource(Protocol):
    source: SourceDescriptor

    def read(self) -> Frame | None: ...
    def close(self) -> None: ...


def _load_cv2() -> Any:
    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError as exc:
        raise CaptureUnavailable(
            'OpenCV capture support is not installed; install VisionRig with ".[capture]"'
        ) from exc
    return cv2


class ImageFileSource:
    def __init__(self, path: str | Path, *, source_id: str = "image-file") -> None:
        self.path = Path(path)
        self.source = SourceDescriptor(
            source_id=source_id,
            source_type="image",
            device=str(self.path),
        )
        self._read = False

    def read(self) -> Frame | None:
        if self._read:
            return None
        cv2 = _load_cv2()
        image = cv2.imread(str(self.path))
        if image is None:
            raise CaptureReadError(f"unable to decode image: {self.path}")
        self._read = True
        return Frame(source=self.source, sequence=0, payload=image)

    def close(self) -> None:
        self._read = True


class OpenCVStreamSource:
    """Camera or video-file source using cv2.VideoCapture."""

    def __init__(
        self,
        origin: int | str | Path,
        *,
        source_id: str,
        source_type: str,
    ) -> None:
        if source_type not in {"camera", "video"}:
            raise ValueError("source_type must be camera or video")
        cv2 = _load_cv2()
        capture_origin = str(origin) if isinstance(origin, Path) else origin
        self._capture = cv2.VideoCapture(capture_origin)
        if not self._capture.isOpened():
            self._capture.release()
            raise CaptureUnavailable(f"unable to open {source_type} source: {origin}")
        self.source = SourceDescriptor(
            source_id=source_id,
            source_type=source_type,
            device=str(origin),
        )
        self._sequence = 0
        self._closed = False

    def read(self) -> Frame | None:
        if self._closed:
            return None
        ok, image = self._capture.read()
        if not ok:
            return None
        frame = Frame(
            source=self.source,
            sequence=self._sequence,
            payload=image,
        )
        self._sequence += 1
        return frame

    def close(self) -> None:
        if not self._closed:
            self._capture.release()
            self._closed = True


class CameraSource(OpenCVStreamSource):
    def __init__(self, index: int = 0, *, source_id: str = "camera-0") -> None:
        super().__init__(index, source_id=source_id, source_type="camera")


class VideoFileSource(OpenCVStreamSource):
    def __init__(self, path: str | Path, *, source_id: str = "video-file") -> None:
        super().__init__(Path(path), source_id=source_id, source_type="video")
