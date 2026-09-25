from hashlib import sha256
import json
from pathlib import Path

import pytest

from visionrig.model_manifest import (
    EmbeddingModelManifest,
    ModelManifestError,
    load_verified_depth_model,
    load_verified_embedding_model,
    load_verified_yolo_model,
)


def _write(tmp_path: Path, payload: dict, artifact: bytes = b"fake-onnx") -> Path:
    model = tmp_path / payload["artifact"]
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(artifact)
    payload = dict(payload)
    payload["sha256"] = payload.get("sha256", sha256(artifact).hexdigest())
    path = tmp_path / "model.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_yolo_manifest_verifies_artifact_checksum(tmp_path: Path) -> None:
    path = _write(tmp_path, {
        "schema_id": "visionrig/yolo-model-manifest/v1",
        "artifact": "model.onnx",
        "source": "unit-test fixture",
        "artifact_license": "test-only",
        "labels": ["person", "cup"],
    })
    verified = load_verified_yolo_model(path)
    assert verified.artifact_path.name == "model.onnx"


def test_depth_manifest_is_typed(tmp_path: Path) -> None:
    path = _write(tmp_path, {
        "schema_id": "visionrig/depth-model-manifest/v1",
        "artifact": "depth.onnx",
        "source": "unit-test fixture",
        "artifact_license": "test-only",
        "higher_is_nearer": True,
    })
    verified = load_verified_depth_model(path)
    assert verified.manifest.schema_id == "visionrig/depth-model-manifest/v1"


def test_embedding_manifest_carries_model_identity(tmp_path: Path) -> None:
    path = _write(tmp_path, {
        "schema_id": "visionrig/embedding-model-manifest/v1",
        "artifact": "embed.onnx",
        "source": "unit-test fixture",
        "artifact_license": "test-only",
        "model_id": "vision-embed/test-v1",
    })
    verified = load_verified_embedding_model(path)
    assert isinstance(verified.manifest, EmbeddingModelManifest)
    assert verified.manifest.model_id == "vision-embed/test-v1"


def test_manifest_rejects_checksum_mismatch(tmp_path: Path) -> None:
    path = _write(tmp_path, {
        "schema_id": "visionrig/yolo-model-manifest/v1",
        "artifact": "model.onnx",
        "sha256": "0" * 64,
        "source": "unit-test fixture",
        "artifact_license": "test-only",
        "labels": ["person"],
    })
    with pytest.raises(ModelManifestError, match="checksum mismatch"):
        load_verified_yolo_model(path)


def test_manifest_rejects_artifact_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.onnx"
    outside.write_bytes(b"fake")
    path = tmp_path / "model.json"
    path.write_text(json.dumps({
        "schema_id": "visionrig/yolo-model-manifest/v1",
        "artifact": "../outside.onnx",
        "sha256": sha256(b"fake").hexdigest(),
        "source": "unit-test fixture",
        "artifact_license": "test-only",
        "labels": ["person"],
    }), encoding="utf-8")
    with pytest.raises(ModelManifestError, match="inside the manifest directory"):
        load_verified_yolo_model(path)
