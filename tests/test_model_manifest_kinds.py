from hashlib import sha256
import json
from pathlib import Path

import pytest

from visionrig.model_manifest import ModelManifestError, load_verified_yolo_model


def write_manifest(tmp_path: Path, *, label_kinds: dict[str, str]) -> Path:
    artifact = tmp_path / "detector.onnx"
    artifact.write_bytes(b"fake")
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_id": "visionrig/yolo-model-manifest/v1",
                "artifact": "detector.onnx",
                "sha256": sha256(b"fake").hexdigest(),
                "source": "test",
                "artifact_license": "test-only",
                "labels": ["person", "face", "cup"],
                "label_kinds": label_kinds,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_manifest_accepts_explicit_label_kinds(tmp_path: Path) -> None:
    verified = load_verified_yolo_model(
        write_manifest(tmp_path, label_kinds={"face": "face"})
    )
    assert verified.manifest.label_kinds["face"] == "face"


def test_manifest_rejects_kind_for_unknown_label(tmp_path: Path) -> None:
    with pytest.raises(ModelManifestError):
        load_verified_yolo_model(
            write_manifest(tmp_path, label_kinds={"not-in-labels": "face"})
        )


def test_manifest_rejects_unknown_entity_kind(tmp_path: Path) -> None:
    with pytest.raises(ModelManifestError):
        load_verified_yolo_model(
            write_manifest(tmp_path, label_kinds={"face": "human-ish"})
        )
