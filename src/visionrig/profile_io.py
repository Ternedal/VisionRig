"""Local encrypted .mrvision profile loading for service runtime."""
from __future__ import annotations

import base64
from pathlib import Path

from .profile import MrVisionError, MrVisionProfile, open_profile


class ProfileLoadError(MrVisionError):
    pass


def read_profile_key(path: str | Path) -> bytes:
    key_path = Path(path)
    try:
        raw = key_path.read_bytes()
    except OSError as exc:
        raise ProfileLoadError(f"unable to read .mrvision key file: {key_path}") from exc

    if len(raw) == 32:
        return raw

    try:
        text = raw.decode("ascii").strip()
        decoded = base64.b64decode(text, validate=True)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProfileLoadError(
            ".mrvision key file must contain 32 raw bytes or base64 for 32 bytes"
        ) from exc

    if len(decoded) != 32:
        raise ProfileLoadError(
            ".mrvision key file must contain 32 raw bytes or base64 for 32 bytes"
        )
    return decoded


def load_encrypted_profile(
    profile_path: str | Path,
    key_path: str | Path,
) -> MrVisionProfile:
    profile_file = Path(profile_path)
    try:
        blob = profile_file.read_bytes()
    except OSError as exc:
        raise ProfileLoadError(
            f"unable to read .mrvision profile: {profile_file}"
        ) from exc

    key = read_profile_key(key_path)
    try:
        return open_profile(blob, key)
    except MrVisionError as exc:
        raise ProfileLoadError(
            f"unable to authenticate/decrypt .mrvision profile: {profile_file}"
        ) from exc
