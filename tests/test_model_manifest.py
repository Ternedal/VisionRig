from hashlib import sha256
import json
from pathlib import Path

import pytest

from visionrig.model_manifest import ModelManifestError, load_verified_yolo_model


def _write_manifest(tmp_path: Path, artifact: bytes, checksum: str | None = None) -> Path:
    model = tmp_path / "model.onnx"
    model.write_bytes(artifact)
    path = tmp_path / "model.json"
    path.write_text(
        json.dumps({
            "schema_id": "visionrig/yolo-model-manifest/v1",
            "artifact": "model.onnx",
            "sha256": checksum or sha256(artifact).hexdigest(),
            "source": "unit-test fixture",
            "artifact_license": "test-only",
            "labels": ["person", "cup"],
            "input_size": 640,
            "confidence_threshold": 0.25,
            "iou_threshold": 0.45
        }),
        encoding="utf-8",
    )
    return path


def test_manifest_verifies_artifact_checksum(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, b"fake-onnx")
    verified = load_verified_yolo_model(path)
    assert verified.artifact_path.name == "model.onnx"
    assert verified.manifest.labels == ("person", "cup")


def test_manifest_rejects_checksum_mismatch(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, b"fake-onnx", "0" * 64)
    with pytest.raises(ModelManifestError, match="checksum mismatch"):
        load_verified_yolo_model(path)
