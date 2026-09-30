from __future__ import annotations

import json
from pathlib import Path

import pytest

from visionrig.physical_qualification import _attach_release_evidence_ref
from visionrig.release_evidence import build_release_evidence_bundle
from visionrig.release_promotion import (
    DEFAULT_TARGET_VERSION,
    OFFICIAL_REPOSITORY,
    PROMOTION_SCHEMA,
    REQUIRED_CI_JOBS,
    ReleasePromotionError,
    build_promotion_attestation,
    validate_ci_run,
    validate_promotion_attestation,
)

SHA = "a" * 40
VERSION = "0.99.0"


def _generic_report() -> dict[str, object]:
    event_ref = "visionrig-event:" + "b" * 64
    report: dict[str, object] = {
        "schema": "visionrig/physical-perception-qualification/v2",
        "generated_at": "2026-09-30T18:00:00Z",
        "visionrig": {
            "origin": "http://127.0.0.1:8110",
            "health_schema": "visionrig/health/v67",
            "service_instance_id": "visionrig-instance:test",
            "service_version": VERSION,
            "service_revision": SHA,
            "perception_schema": "visionrig/perception-event/v4",
        },
        "physical_source": {
            "source_id": "kaliv-android",
            "source_type": "camera",
            "device": "android-camera",
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


def _bundle_file(tmp_path: Path) -> Path:
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(_generic_report(), sort_keys=True),
        encoding="utf-8",
    )
    bundle = build_release_evidence_bundle(
        [receipt],
        expected_sha=SHA,
        expected_version=VERSION,
        require_clean_checkout=False,
    )
    path = tmp_path / "bundle.json"
    path.write_text(
        json.dumps(bundle, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _ci_run(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": 123456,
        "name": "tests",
        "path": ".github/workflows/tests.yml",
        "event": "push",
        "head_branch": "main",
        "head_sha": SHA,
        "status": "completed",
        "conclusion": "success",
        "run_attempt": 1,
        "html_url": (
            "https://github.com/Ternedal/VisionRig/actions/runs/123456"
        ),
    }
    value.update(changes)
    return value


def _ci_jobs() -> list[dict[str, object]]:
    return [
        {
            "id": index + 100,
            "name": name,
            "head_sha": SHA,
            "status": "completed",
            "conclusion": "success",
        }
        for index, name in enumerate(REQUIRED_CI_JOBS)
    ]


def test_validates_exact_green_main_ci_run() -> None:
    summary = validate_ci_run(
        _ci_run(),
        _ci_jobs(),
        expected_sha=SHA,
    )

    assert summary["repository"] == OFFICIAL_REPOSITORY
    assert summary["head_sha"] == SHA
    assert [job["name"] for job in summary["required_jobs"]] == list(
        REQUIRED_CI_JOBS
    )


def test_rejects_pull_request_run_as_release_ci() -> None:
    with pytest.raises(ReleasePromotionError, match="push run"):
        validate_ci_run(
            _ci_run(event="pull_request"),
            _ci_jobs(),
            expected_sha=SHA,
        )


def test_rejects_wrong_main_revision() -> None:
    with pytest.raises(ReleasePromotionError, match="revision mismatch"):
        validate_ci_run(
            _ci_run(head_sha="b" * 40),
            _ci_jobs(),
            expected_sha=SHA,
        )


def test_rejects_missing_required_platform_job() -> None:
    jobs = _ci_jobs()
    jobs = [job for job in jobs if job["name"] != "quest-producer"]

    with pytest.raises(ReleasePromotionError, match="required CI job is missing"):
        validate_ci_run(
            _ci_run(),
            jobs,
            expected_sha=SHA,
        )


def test_rejects_failed_required_platform_job() -> None:
    jobs = _ci_jobs()
    jobs[-1]["conclusion"] = "failure"

    with pytest.raises(ReleasePromotionError, match="required CI job is not green"):
        validate_ci_run(
            _ci_run(),
            jobs,
            expected_sha=SHA,
        )


def test_builds_content_addressed_v1_promotion_attestation(
    tmp_path: Path,
) -> None:
    attestation = build_promotion_attestation(
        _bundle_file(tmp_path),
        expected_sha=SHA,
        expected_candidate_version=VERSION,
        target_version=DEFAULT_TARGET_VERSION,
        ci_run=_ci_run(),
        ci_jobs=_ci_jobs(),
    )

    assert attestation["schema"] == PROMOTION_SCHEMA
    assert attestation["candidate_version"] == VERSION
    assert attestation["target_version"] == "1.0.0"
    assert attestation["visionrig_git_sha"] == SHA
    assert attestation["gate"] == {
        "physical_evidence_validated": True,
        "repository_ci_verified": True,
        "release_ready": True,
        "production_activation": False,
    }
    assert str(attestation["promotion_ref"]).startswith(
        "visionrig-release-promotion:" + SHA + ":"
    )
    validate_promotion_attestation(attestation)


def test_rejects_bundle_for_different_candidate_version(tmp_path: Path) -> None:
    with pytest.raises(ReleasePromotionError, match="bundle version mismatch"):
        build_promotion_attestation(
            _bundle_file(tmp_path),
            expected_sha=SHA,
            expected_candidate_version="0.98.0",
            ci_run=_ci_run(),
            ci_jobs=_ci_jobs(),
        )


def test_rejects_tampered_physical_bundle(tmp_path: Path) -> None:
    path = _bundle_file(tmp_path)
    bundle = json.loads(path.read_text(encoding="utf-8"))
    bundle["evidence"][0]["receipt_bytes"] += 1
    path.write_text(json.dumps(bundle), encoding="utf-8")

    with pytest.raises(
        ReleasePromotionError,
        match="physical release bundle integrity failed",
    ):
        build_promotion_attestation(
            path,
            expected_sha=SHA,
            expected_candidate_version=VERSION,
            ci_run=_ci_run(),
            ci_jobs=_ci_jobs(),
        )


def test_promotion_ref_detects_ci_job_tampering(tmp_path: Path) -> None:
    attestation = build_promotion_attestation(
        _bundle_file(tmp_path),
        expected_sha=SHA,
        expected_candidate_version=VERSION,
        ci_run=_ci_run(),
        ci_jobs=_ci_jobs(),
    )
    attestation["repository_ci"]["required_jobs"][0]["job_id"] += 1

    with pytest.raises(ReleasePromotionError, match="promotion ref hash mismatch"):
        validate_promotion_attestation(attestation)


def test_release_promotion_never_grants_production_activation(
    tmp_path: Path,
) -> None:
    attestation = build_promotion_attestation(
        _bundle_file(tmp_path),
        expected_sha=SHA,
        expected_candidate_version=VERSION,
        ci_run=_ci_run(),
        ci_jobs=_ci_jobs(),
    )
    attestation["gate"]["production_activation"] = True

    with pytest.raises(ReleasePromotionError, match="gate contract mismatch"):
        validate_promotion_attestation(attestation)
