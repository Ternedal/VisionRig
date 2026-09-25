from pathlib import Path

import pytest

import visionrig.sources as sources


class _FakeImageCV2:
    @staticmethod
    def imread(path: str):
        return {"decoded": path}


def test_image_source_is_one_shot(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sources, "_load_cv2", lambda: _FakeImageCV2())
    path = tmp_path / "frame.jpg"
    source = sources.ImageFileSource(path)

    frame = source.read()
    assert frame is not None
    assert frame.sequence == 0
    assert frame.payload == {"decoded": str(path)}
    assert source.read() is None


def test_invalid_stream_type_fails_before_capture() -> None:
    with pytest.raises(ValueError):
        sources.OpenCVStreamSource(0, source_id="x", source_type="screen")
