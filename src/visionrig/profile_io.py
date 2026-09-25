"""Local encrypted .mrvision profile loading for service runtime."""
from __future__ import annotations

import base64
from pathlib import Path

from .profile import (
    MrVisionError,
    MrVisionProfile,
    generate_profile_key,
    open_profile,
    seal_profile,
)


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


def write_profile_key(path: str | Path, key: bytes) -> None:
    if not isinstance(key, bytes) or len(key) != 32:
        raise ProfileLoadError("profile key must be exactly 32 bytes")
    key_path = Path(path)
    if key_path.exists():
        raise ProfileLoadError(f"refusing to overwrite existing key file: {key_path}")
    key_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = key_path.with_name(key_path.name + ".tmp")
    try:
        temporary.write_bytes(key)
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(key_path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ProfileLoadError(f"unable to write .mrvision key file: {key_path}") from exc


def save_encrypted_profile(
    profile: MrVisionProfile,
    profile_path: str | Path,
    key_path: str | Path,
) -> None:
    if not isinstance(profile, MrVisionProfile):
        raise TypeError("profile must be MrVisionProfile")
    destination = Path(profile_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    key = read_profile_key(key_path)
    blob = seal_profile(profile, key)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        temporary.write_bytes(blob)
        temporary.replace(destination)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ProfileLoadError(
            f"unable to write .mrvision profile: {destination}"
        ) from exc


def initialize_encrypted_profile(
    profile: MrVisionProfile,
    profile_path: str | Path,
    key_path: str | Path,
) -> None:
    destination = Path(profile_path)
    key_destination = Path(key_path)
    if destination.exists():
        raise ProfileLoadError(
            f"refusing to overwrite existing .mrvision profile: {destination}"
        )
    key = generate_profile_key()
    write_profile_key(key_destination, key)
    try:
        blob = seal_profile(profile, key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_bytes(blob)
        temporary.replace(destination)
    except Exception:
        try:
            key_destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise
