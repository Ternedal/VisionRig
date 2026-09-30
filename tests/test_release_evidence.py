from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from visionrig.kinect_acceptance import KinectPhysicalAcceptanceReceipt
from visionrig.physical_qualification import _attach_release_evidence_ref
from visionrig.release_evidence import (
    BUNDLE_SCHEMA,
    ReleaseEvidenceError,
    build_release_evidence_bundle,
    validate_bundle_integrity,
    validate_evidence,
)

SHA = "a" * 40
VERSION = "0.99.0"


def _generic_report(
    *,
    source_id: str = "kaliv-android",
    source_type: str = "camera",
) -> dict[str, object]:
    event_ref = "visionrig-event:" + "b" * 64
    report: dict[str, object] = {
        "schema": "visionrig/physical-perception-qualification/v2",
        "generated_at": "2026-09-30T15:00:00Z",
        "visionrig": {
            "origin": "http://127.0.0.1:8110",
            "health_schema": "visionrig/health/v67",
            "service_instance_id": "visionrig-instance:test",
            "service_version": VERSION,
            "service_revision": SHA,
            "perception_schema": "visionrig/perception-event/v4",
        },
        "physical_source": {
            "source_id": source_id,
            "source_type": source_type,
            "device": "test-device",
            "presence": "online",
            "baseline_sequence": 10,
            "accepted_frames_before": 10,
            "accepted_frames_after": 11,
            "last_sequence_after": 11,
        },
        "event": {
            "journal_cursor": 4,
            "frame_sequence": 11,
            "visionrig_event_ref": event_ref,
            "semantic_observation_count": 1,
            "semantic_observation_kinds": ["entities"],
            "semantic_observation_counts": {
                "entities": 1,
                "relations": 0,
                "landmarks": 0,
                "depth": 0,
                "infrared": 0,
                "scene": 0,
            },
        },
        "modelrig_admission": {
            "status": "published",
            "visionrig_event_ref": event_ref,
            "evidence_ref": "world-evidence-event:" + "c" * 64,
            "cognition_event_id": "cevt-" + "d" * 32,
            "world_changed": True,
            "replayed": False,
            "cognition_event_queued": True,
            "observed_sequence": 11,
            "model_calls": 0,
        },
        "timing": {
            "qualification_elapsed_ms": 12.0,
            "poll_interval_ms": 250.0,
        },
        "privacy": {
            "raw_frame_included": False,
            "ocr_text_included": False,
            "landmarks_included": False,
            "semantic_payload_included": False,
        },
        "gate": {
            "passed": True,
            "physical_perception_qualified": True,
            "raw_sensor_authority_granted": False,
            "identity_authority_granted": False,
            "production_activation": False,
        },
    }
    return _attach_release_evidence_ref(report)


def _kinect_report() -> dict[str, object]:
    receipt = KinectPhysicalAcceptanceReceipt(
        schema="visionrig/kinect-physical-acceptance/v3",
        generated_at=datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc),
        visionrig_version=VERSION,
        visionrig_git_sha=SHA,
        release_gate="visionrig_physical_perception",
        source_id="kinect-v2-acceptance",
        device="kinect-v2",
        requested_frames=5,
        captured_frames=5,
        first_frame_sequence=100,
        last_frame_sequence=104,
        sequences_strictly_contiguous=True,
        rgb_frames=5,
        raw_depth_frames=5,
        aligned_depth_frames=5,
        infrared_frames=5,
        infrared_semantic_frames=5,
        infrared_observations=5,
        perception_schema="visionrig/perception-event/v4",
        semantic_events=2,
        semantic_observations=3,
        modelrig_receipts=2,
        modelrig_world_changed_receipts=2,
        modelrig_cognition_queued_receipts=2,
        modelrig_event_refs=("visionrig-event:" + "e" * 64,),
        modelrig_evidence_refs=("world-evidence-event:" + "f" * 64,),
        modelrig_cognition_event_ids=("cevt-" + "1" * 32,),
        real_sensor_required=True,
        exact_checkout_required=True,
        raw_frames_persisted=False,
        identity_authority=False,
        durable_memory_write_authority=False,
        execution_authority=False,
        scheduling_authority=False,
        production_authority=False,
        passed=True,
    )
    return receipt.model_dump(mode="json")


def _write(path: Path, value: dict[str, object]) -> Path:
    import json

    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return path


def test_validates_generic_physical_release_ref() -> None:
    summary = validate_evidence(
        _generic_report(),
        expected_sha=SHA,
        expected_version=VERSION,
    )
    assert summary["schema"] == "visionrig/physical-perception-qualification/v2"
    assert summary["source_id"] == "kaliv-android"
    assert str(summary["release_evidence_ref"]).startswith(
        "visionrig-physical-perception:" + SHA + ":"
    )


def test_rejects_tampered_generic_release_ref() -> None:
    report = _generic_report()
    report["release_evidence_ref"] = (
        "visionrig-physical-perception:" + SHA + ":" + "0" * 64
    )
    with pytest.raises(ReleaseEvidenceError, match="hash mismatch"):
        validate_evidence(
            report,
            expected_sha=SHA,
            expected_version=VERSION,
        )


def test_rejects_generic_version_mismatch() -> None:
    with pytest.raises(ReleaseEvidenceError, match="version"):
        validate_evidence(
            _generic_report(),
            expected_sha=SHA,
            expected_version="1.0.0",
        )


def test_validates_kinect_v3_receipt() -> None:
    summary = validate_evidence(
        _kinect_report(),
        expected_sha=SHA,
        expected_version=VERSION,
    )
    assert summary["schema"] == "visionrig/kinect-physical-acceptance/v3"
    assert summary["device"] == "kinect-v2"
    assert summary["captured_frames"] == 5


def test_rejects_kinect_sha_mismatch() -> None:
    with pytest.raises(ReleaseEvidenceError, match="revision"):
        validate_evidence(
            _kinect_report(),
            expected_sha="2" * 40,
            expected_version=VERSION,
        )


def test_builds_bundle_without_claiming_ci_verification(tmp_path: Path) -> None:
    android = _write(tmp_path / "android.json", _generic_report())
    kinect = _write(tmp_path / "kinect.json", _kinect_report())

    bundle = build_release_evidence_bundle(
        [android, kinect],
        expected_sha=SHA,
        expected_version=VERSION,
        require_clean_checkout=False,
    )

    assert bundle["schema"] == BUNDLE_SCHEMA
    assert bundle["evidence_count"] == 2
    assert str(bundle["bundle_ref"]).startswith(
        "visionrig-release-evidence:" + SHA + ":"
    )
    assert bundle["gate"] == {
        "physical_evidence_validated": True,
        "repository_ci_required": True,
        "repository_ci_verified_by_this_tool": False,
        "production_activation": False,
    }
    for item in bundle["evidence"]:
        assert str(item["receipt_sha256"]).startswith("sha256:")
        assert item["receipt_bytes"] > 0

    validate_bundle_integrity(bundle)


def test_rejects_duplicate_source_evidence(tmp_path: Path) -> None:
    first = _write(tmp_path / "a.json", _generic_report())
    second = _write(tmp_path / "b.json", _generic_report())

    with pytest.raises(ReleaseEvidenceError, match="duplicate physical evidence"):
        build_release_evidence_bundle(
            [first, second],
            expected_sha=SHA,
            expected_version=VERSION,
            require_clean_checkout=False,
        )


def test_receipt_digest_binds_exact_file_bytes(tmp_path: Path) -> None:
    import hashlib

    path = _write(tmp_path / "android.json", _generic_report())
    raw = path.read_bytes()

    bundle = build_release_evidence_bundle(
        [path],
        expected_sha=SHA,
        expected_version=VERSION,
        require_clean_checkout=False,
    )

    item = bundle["evidence"][0]
    assert item["receipt_sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    assert item["receipt_bytes"] == len(raw)


def test_bundle_ref_is_independent_of_evidence_path(tmp_path: Path) -> None:
    first_dir = tmp_path / "one"
    second_dir = tmp_path / "two"
    first_dir.mkdir()
    second_dir.mkdir()
    first = _write(first_dir / "receipt.json", _generic_report())
    second = _write(second_dir / "receipt.json", _generic_report())

    first_bundle = build_release_evidence_bundle(
        [first],
        expected_sha=SHA,
        expected_version=VERSION,
        require_clean_checkout=False,
    )
    second_bundle = build_release_evidence_bundle(
        [second],
        expected_sha=SHA,
        expected_version=VERSION,
        require_clean_checkout=False,
    )

    assert first_bundle["bundle_ref"] == second_bundle["bundle_ref"]


def test_bundle_integrity_detects_bound_metadata_tampering(tmp_path: Path) -> None:
    path = _write(tmp_path / "android.json", _generic_report())
    bundle = build_release_evidence_bundle(
        [path],
        expected_sha=SHA,
        expected_version=VERSION,
        require_clean_checkout=False,
    )
    bundle["evidence"][0]["receipt_bytes"] += 1

    with pytest.raises(ReleaseEvidenceError, match="ref hash mismatch"):
        validate_bundle_integrity(bundle)
