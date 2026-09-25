from pathlib import Path

import pytest

from visionrig.service_config import ServiceConfig, ServiceConfigError


def test_profile_and_key_must_be_configured_together(monkeypatch) -> None:
    monkeypatch.setenv("VISIONRIG_MRVISION_PROFILE", "profile.mrvision")
    monkeypatch.delenv("VISIONRIG_MRVISION_KEY_FILE", raising=False)
    with pytest.raises(ServiceConfigError, match="configured together"):
        ServiceConfig.from_env()


def test_profile_requires_embedding_manifest(monkeypatch) -> None:
    monkeypatch.setenv("VISIONRIG_MRVISION_PROFILE", "profile.mrvision")
    monkeypatch.setenv("VISIONRIG_MRVISION_KEY_FILE", "profile.key")
    monkeypatch.delenv("VISIONRIG_EMBEDDING_MANIFEST", raising=False)
    with pytest.raises(ServiceConfigError, match="requires"):
        ServiceConfig.from_env()


def test_recognition_threshold_is_bounded(monkeypatch) -> None:
    monkeypatch.setenv("VISIONRIG_RECOGNITION_THRESHOLD", "1.1")
    with pytest.raises(ServiceConfigError, match="between 0 and 1"):
        ServiceConfig.from_env()


def test_runtime_loads_encrypted_profile_from_files(
    monkeypatch,
    tmp_path: Path,
) -> None:
    # load_profile wiring is tested independently from cryptography details.
    monkeypatch.setenv("VISIONRIG_MRVISION_PROFILE", str(tmp_path / "p.mrvision"))
    monkeypatch.setenv("VISIONRIG_MRVISION_KEY_FILE", str(tmp_path / "p.key"))
    monkeypatch.setenv("VISIONRIG_EMBEDDING_MANIFEST", "embed.json")

    config = ServiceConfig.from_env()
    assert config.mrvision_profile == str(tmp_path / "p.mrvision")
    assert config.mrvision_key_file == str(tmp_path / "p.key")
    assert config.recognition_threshold == 0.75
