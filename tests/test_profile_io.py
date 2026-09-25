import base64
from pathlib import Path

import pytest

from visionrig.profile import create_profile, generate_profile_key, seal_profile
from visionrig.profile_io import (
    ProfileLoadError,
    load_encrypted_profile,
    read_profile_key,
)


def test_profile_loader_accepts_raw_key(tmp_path: Path) -> None:
    profile = create_profile()
    key = generate_profile_key()
    profile_path = tmp_path / "person.mrvision"
    key_path = tmp_path / "person.key"
    profile_path.write_bytes(seal_profile(profile, key))
    key_path.write_bytes(key)

    assert load_encrypted_profile(profile_path, key_path) == profile


def test_profile_loader_accepts_base64_key_file(tmp_path: Path) -> None:
    key = generate_profile_key()
    key_path = tmp_path / "person.key"
    key_path.write_text(base64.b64encode(key).decode("ascii") + "\n", encoding="ascii")
    assert read_profile_key(key_path) == key


def test_profile_loader_rejects_bad_key_material(tmp_path: Path) -> None:
    key_path = tmp_path / "bad.key"
    key_path.write_text("not-a-valid-key", encoding="ascii")
    with pytest.raises(ProfileLoadError):
        read_profile_key(key_path)
