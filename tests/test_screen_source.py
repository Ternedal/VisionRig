import numpy as np

from visionrig.screen_source import _bgr_from_mss


def test_mss_bgra_frame_becomes_contiguous_bgr() -> None:
    raw = np.zeros((3, 4, 4), dtype=np.uint8)
    raw[:, :, 0] = 10
    raw[:, :, 1] = 20
    raw[:, :, 2] = 30
    raw[:, :, 3] = 255

    result = _bgr_from_mss(raw)

    assert result.shape == (3, 4, 3)
    assert result.flags["C_CONTIGUOUS"]
    assert tuple(result[0, 0]) == (10, 20, 30)
