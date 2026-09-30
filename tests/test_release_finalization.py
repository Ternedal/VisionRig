from __future__ import annotations

import json
from pathlib import Path

import pytest

from visionrig.release_finalization import (
    CANDIDATE_VERSION,
    FINALIZATION_SCHEMA,
    RELEASE_VERSION,
    ReleaseFinalizationError,
    build_finalization_attestation,
    validate_finalization_attestation,
    validate_release_changed_paths,
    validate_version_file_transition,
)
from visionrig.release_promotion import (
    OFFICIAL_REPOSITORY,
    PROMOTION_SCHEMA,
    REQUIRED_CI_JOBS,
    _expected_promotion_ref,
)

CANDIDATE_SHA = "a" * 40
RELEASE_SHA = "b" * 40


def _ci_run() -> dict[str, object]:
    return {
        "id": 9001,
        "name": "tests",
        "path": ".github/workflows/tests.yml",
        "event": "push",
        "head_branch": "main",
        "head_sha": RELEASE_SHA,
        "status": "completed",
        "conclusion": "success",
        "run_attempt": 1,
        "html_url": (
            "https://github.com/Ternedal/VisionRig/actions/runs/9001"
        ),
    }


def _ci_jobs() -> list[dict[str, object]]:
    return [
        {
            "id": index + 500,
            "name": name,
            "head_sha": RELEASE_SHA,
            "status": "completed",
            "conclusion": "success",
        }
        for index, name in enumerate(REQUIRED_CI_JOBS)
    ]


def _promotion() -> dict[str, object]:
    ci_summary = {
        "repository": OFFICIAL_REPOSITORY,
        "workflow_name": "tests",
        "workflow_path": ".github/workflows/tests.yml",
        "event": "push",
        "branch": "main",
        "run_id": 8001,
        "run_attempt": 1,
        "run_url": (
            "https://github.com/Ternedal/VisionRig/actions/runs/8001"
        ),
        "head_sha": CANDIDATE_SHA,
        "required_jobs": [
            {
                "name": name,
                "job_id": index + 100,
                "conclusion": "success",
            }
            for index, name in enumerate(REQUIRED_CI_JOBS)
        ],
    }
    value: dict[str, object] = {
        "schema": PROMOTION_SCHEMA,
        "generated_at": "2026-09-30T18:00:00Z",
        "repository": OFFICIAL_REPOSITORY,
        "candidate_version": CANDIDATE_VERSION,
        "target_version": RELEASE_VERSION,
        "visionrig_git_sha": CANDIDATE_SHA,
        "physical_evidence": {
            "bundle_ref": (
                "visionrig-release-evidence:"
                + CANDIDATE_SHA
                + ":"
                + "c" * 64
            ),
            "bundle_sha256": "sha256:" + "d" * 64,
            "bundle_bytes": 123,
            "evidence_count": 1,
        },
        "repository_ci": ci_summary,
        "gate": {
            "physical_evidence_validated": True,
            "repository_ci_verified": True,
            "release_ready": True,
            "production_activation": False,
        },
    }
    value["promotion_ref"] = _expected_promotion_ref(value)
    return value


def _promotion_file(tmp_path: Path) -> Path:
    path = tmp_path / "promotion.json"
    path.write_text(
        json.dumps(_promotion(), sort_keys=True),
        encoding="utf-8",
    )
    return path


def _checkout() -> dict[str, object]:
    return {
        "candidate_sha": CANDIDATE_SHA,
        "release_sha": RELEASE_SHA,
        "changed_paths": [
            "pyproject.toml",
            "src/visionrig/__init__.py",
            "tests/test_version.py",
        ],
        "candidate_version": CANDIDATE_VERSION,
        "release_version": RELEASE_VERSION,
    }


def test_release_changed_paths_require_only_version_contract() -> None:
    assert validate_release_changed_paths(
        [
            "tests/test_version.py",
            "src/visionrig/__init__.py",
            "pyproject.toml",
            "docs/RELEASE.md",
        ]
    ) == (
        "docs/RELEASE.md",
        "pyproject.toml",
        "src/visionrig/__init__.py",
        "tests/test_version.py",
    )


def test_release_changed_paths_reject_code_change() -> None:
    with pytest.raises(
        ReleaseFinalizationError,
        match="non-release files",
    ):
        validate_release_changed_paths(
            [
                "pyproject.toml",
                "src/visionrig/__init__.py",
                "tests/test_version.py",
                "src/visionrig/api.py",
            ]
        )


def test_release_changed_paths_require_all_version_files() -> None:
    with pytest.raises(
        ReleaseFinalizationError,
        match="lacks required version files",
    ):
        validate_release_changed_paths(
            [
                "pyproject.toml",
                "src/visionrig/__init__.py",
            ]
        )


def test_validates_exact_version_file_transition() -> None:
    candidate = {
        "pyproject.toml": '[project]\nversion = "0.99.0"\nname = "visionrig"\n',
        "src/visionrig/__init__.py": '__version__ = "0.99.0"\n',
        "tests/test_version.py": (
            "def test_release_candidate_version() -> None:\n"
            '    assert __version__ == "0.99.0"\n'
        ),
    }
    release = {
        "pyproject.toml": '[project]\nversion = "1.0.0"\nname = "visionrig"\n',
        "src/visionrig/__init__.py": '__version__ = "1.0.0"\n',
        "tests/test_version.py": (
            "def test_v1_release_version() -> None:\n"
            '    assert __version__ == "1.0.0"\n'
        ),
    }

    validate_version_file_transition(candidate, release)


def test_rejects_extra_change_inside_version_file() -> None:
    candidate = {
        "pyproject.toml": '[project]\nversion = "0.99.0"\nname = "visionrig"\n',
        "src/visionrig/__init__.py": '__version__ = "0.99.0"\n',
        "tests/test_version.py": (
            "def test_release_candidate_version() -> None:\n"
            '    assert __version__ == "0.99.0"\n'
        ),
    }
    release = {
        "pyproject.toml": (
            '[project]\nversion = "1.0.0"\nname = "visionrig-renamed"\n'
        ),
        "src/visionrig/__init__.py": '__version__ = "1.0.0"\n',
        "tests/test_version.py": (
            "def test_v1_release_version() -> None:\n"
            '    assert __version__ == "1.0.0"\n'
        ),
    }

    with pytest.raises(
        ReleaseFinalizationError,
        match="changes more than version contract",
    ):
        validate_version_file_transition(candidate, release)


def test_builds_revision_bound_finalization_attestation(tmp_path: Path) -> None:
    attestation = build_finalization_attestation(
        _promotion_file(tmp_path),
        checkout=_checkout(),
        ci_run=_ci_run(),
        ci_jobs=_ci_jobs(),
    )

    assert attestation["schema"] == FINALIZATION_SCHEMA
    assert attestation["candidate_sha"] == CANDIDATE_SHA
    assert attestation["release_sha"] == RELEASE_SHA
    assert attestation["candidate_version"] == "0.99.0"
    assert attestation["release_version"] == "1.0.0"
    assert attestation["gate"] == {
        "candidate_promotion_verified": True,
        "version_only_release_delta": True,
        "repository_ci_verified": True,
        "release_finalized": True,
        "production_activation": False,
    }
    assert str(attestation["finalization_ref"]).startswith(
        "visionrig-release-finalization:" + RELEASE_SHA + ":"
    )
    validate_finalization_attestation(attestation)


def test_rejects_promotion_for_different_parent(tmp_path: Path) -> None:
    checkout = _checkout()
    checkout["candidate_sha"] = "e" * 40

    with pytest.raises(
        ReleaseFinalizationError,
        match="promotion candidate does not match release parent",
    ):
        build_finalization_attestation(
            _promotion_file(tmp_path),
            checkout=checkout,
            ci_run=_ci_run(),
            ci_jobs=_ci_jobs(),
        )


def test_rejects_non_green_release_commit_ci(tmp_path: Path) -> None:
    jobs = _ci_jobs()
    jobs[-1]["conclusion"] = "failure"

    with pytest.raises(
        ReleaseFinalizationError,
        match="release commit CI verification failed",
    ):
        build_finalization_attestation(
            _promotion_file(tmp_path),
            checkout=_checkout(),
            ci_run=_ci_run(),
            ci_jobs=jobs,
        )


def test_standalone_validation_rejects_ci_summary_tampering(
    tmp_path: Path,
) -> None:
    attestation = build_finalization_attestation(
        _promotion_file(tmp_path),
        checkout=_checkout(),
        ci_run=_ci_run(),
        ci_jobs=_ci_jobs(),
    )
    attestation["repository_ci"]["required_jobs"][-1]["conclusion"] = "failure"

    with pytest.raises(
        ReleaseFinalizationError,
        match="release CI summary is invalid",
    ):
        validate_finalization_attestation(attestation)


def test_finalization_never_grants_production_activation(
    tmp_path: Path,
) -> None:
    attestation = build_finalization_attestation(
        _promotion_file(tmp_path),
        checkout=_checkout(),
        ci_run=_ci_run(),
        ci_jobs=_ci_jobs(),
    )
    attestation["gate"]["production_activation"] = True

    with pytest.raises(
        ReleaseFinalizationError,
        match="gate contract mismatch",
    ):
        validate_finalization_attestation(attestation)
