"""Optional desktop screen capture source for the reference producer."""
from __future__ import annotations

from typing import Any

from .contracts import SourceDescriptor
from .pipeline import Frame
from .sources import CaptureUnavailable


def _bgr_from_mss(raw: Any) -> Any:
    try:
        import numpy as np  # type: ignore[import-not-found]
    except ImportError as exc:
        raise CaptureUnavailable(
            'screen capture requires VisionRig ".[producer]"'
        ) from exc
    array = np.asarray(raw)
    if array.ndim != 3 or array.shape[2] < 3:
        raise RuntimeError("unexpected screen capture shape")
    return np.ascontiguousarray(array[:, :, :3])


class MssScreenSource:
    def __init__(self, monitor: int = 1, *, source_id: str = "screen-1") -> None:
        try:
            import mss  # type: ignore[import-not-found]
        except ImportError as exc:
            raise CaptureUnavailable(
                'screen capture requires VisionRig ".[producer]"'
            ) from exc
        if monitor < 0:
            raise ValueError("monitor must be >= 0")
        self._mss = mss.mss()
        if monitor >= len(self._mss.monitors):
            self._mss.close()
            raise CaptureUnavailable(f"screen monitor {monitor} does not exist")
        self._monitor = monitor
        self.source = SourceDescriptor(
            source_id=source_id,
            source_type="screen",
            device=f"monitor:{monitor}",
        )
        self._sequence = 0
        self._closed = False

    def read(self) -> Frame | None:
        if self._closed:
            return None
        raw = self._mss.grab(self._mss.monitors[self._monitor])
        frame = Frame(
            source=self.source,
            sequence=self._sequence,
            payload=_bgr_from_mss(raw),
        )
        self._sequence += 1
        return frame

    def close(self) -> None:
        if not self._closed:
            self._mss.close()
            self._closed = True
