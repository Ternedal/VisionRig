from __future__ import annotations

import hashlib
import json
from pathlib import Path

from visionrig.release_evidence import BUNDLE_SCHEMA, _expected_bundle_ref
from visionrig.release_finalization import (
    FINALIZATION_SCHEMA,
    _expected_finalization_ref,
)
from visionrig.release_promotion import (
    OFFICIAL_REPOSITORY,
    PROMOTION_SCHEMA,
    REQUIRED_CI_JOBS,
    _expected_promotion_ref,
)
from visionrig.release_status import build_release_status

CANDIDATE_SHA = "a" * 40
RELEASE_SHA = "b" * 40
CANDIDATE_VERSION = "0.99.0"
RELEASE_VERSION = "1.0.0"


def _write(path: Path, value: dict[str, object]) -> tuple[Path, str, int]:
    raw = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    path.write_bytes(raw)
    return path, "sha256:" + hashlib.sha256(raw).hexdigest(), len(raw)


def _ci_summary(sha: str, *, run_id: int) -> dict[str, object]:
    return {
        "repository": OFFICIAL_REPOSITORY,
        "workflow_name": "tests",
        "workflow_path": ".github/workflows/tests.yml",
        "event": "push",
        "branch": "main",
        "run_id": run_id,
        "run_attempt": 1,
        "run_url": (
            "https://github.com/Ternedal/VisionRig/actions/runs/"
            + str(run_id)
        ),
        "head_sha": sha,
        "required_jobs": [
            {
                "name": name,
                "job_id": run_id * 10 + index,
                "conclusion": "success",
            }
            for index, name in enumerate(REQUIRED_CI_JOBS)
        ],
    }


def _bundle() -> dict[str, object]:
    value: dict[str, object] = {
        "schema": BUNDLE_SCHEMA,
        "generated_at": "2026-09-30T20:00:00Z",
        "visionrig_version": CANDIDATE_VERSION,
        "visionrig_git_sha": CANDIDATE_SHA,
        "evidence_count": 1,
        "evidence": [
            {
                "schema": "visionrig/physical-perception-qualification/v2",
                "source_id": "kaliv-android",
                "source_type": "camera",
                "receipt_sha256": "sha256:" + "c" * 64,
                "receipt_bytes": 123,
            }
        ],
        "gate": {
            "physical_evidence_validated": True,
            "repository_ci_required": True,
            "repository_ci_verified_by_this_tool": False,
            "production_activation": False,
        },
    }
    value["bundle_ref"] = _expected_bundle_ref(value)
    return value


def _promotion(
    *,
    bundle_ref: str,
    bundle_sha256: str,
    bundle_bytes: int,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": PROMOTION_SCHEMA,
        "generated_at": "2026-09-30T20:05:00Z",
        "repository": OFFICIAL_REPOSITORY,
        "candidate_version": CANDIDATE_VERSION,
        "target_version": RELEASE_VERSION,
        "visionrig_git_sha": CANDIDATE_SHA,
        "physical_evidence": {
            "bundle_ref": bundle_ref,
            "bundle_sha256": bundle_sha256,
            "bundle_bytes": bundle_bytes,
            "evidence_count": 1,
        },
        "repository_ci": _ci_summary(CANDIDATE_SHA, run_id=8001),
        "gate": {
            "physical_evidence_validated": True,
            "repository_ci_verified": True,
            "release_ready": True,
            "production_activation": False,
        },
    }
    value["promotion_ref"] = _expected_promotion_ref(value)
    return value


def _finalization(
    *,
    promotion_ref: str,
    promotion_sha256: str,
    promotion_bytes: int,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": FINALIZATION_SCHEMA,
        "generated_at": "2026-09-30T20:10:00Z",
        "repository": OFFICIAL_REPOSITORY,
        "candidate_version": CANDIDATE_VERSION,
        "release_version": RELEASE_VERSION,
        "candidate_sha": CANDIDATE_SHA,
        "release_sha": RELEASE_SHA,
        "promotion": {
            "promotion_ref": promotion_ref,
            "promotion_sha256": promotion_sha256,
            "promotion_bytes": promotion_bytes,
        },
        "release_delta": {
            "changed_paths": [
                "pyproject.toml",
                "src/visionrig/__init__.py",
                "tests/test_version.py",
            ],
        },
        "repository_ci": _ci_summary(RELEASE_SHA, run_id=9001),
        "gate": {
            "candidate_promotion_verified": True,
            "version_only_release_delta": True,
            "repository_ci_verified": True,
            "release_finalized": True,
            "production_activation": False,
        },
    }
    value["finalization_ref"] = _expected_finalization_ref(value)
    return value


def test_missing_release_artifacts_report_physical_evidence_pending(
    tmp_path: Path,
) -> None:
    status = build_release_status(
        bundle_path=tmp_path / "bundle.json",
        promotion_path=tmp_path / "promotion.json",
        finalization_path=tmp_path / "finalization.json",
        current_version=CANDIDATE_VERSION,
    )

    assert status["phase"] == "physical_evidence_pending"
    assert status["gate"] == {
        "release_ready": False,
        "release_finalized": False,
        "production_activation": False,
    }
    assert status["artifacts"]["physical_evidence"]["state"] == "missing"
    assert status["blockers"] == [
        "revision-bound physical release evidence has not been bundled yet"
    ]


def test_valid_bundle_reports_promotion_pending(tmp_path: Path) -> None:
    bundle_path, _, _ = _write(tmp_path / "bundle.json", _bundle())

    status = build_release_status(
        bundle_path=bundle_path,
        promotion_path=tmp_path / "promotion.json",
        finalization_path=tmp_path / "finalization.json",
        current_version=CANDIDATE_VERSION,
    )

    assert status["phase"] == "promotion_pending"
    assert status["artifacts"]["physical_evidence"]["state"] == "valid"
    assert status["gate"]["release_ready"] is False
    assert "promotion attestation has not been produced yet" in status["blockers"]


def test_status_rejects_promotion_bound_to_different_bundle_bytes(
    tmp_path: Path,
) -> None:
    bundle = _bundle()
    bundle_path, _, bundle_bytes = _write(tmp_path / "bundle.json", bundle)
    promotion = _promotion(
        bundle_ref=str(bundle["bundle_ref"]),
        bundle_sha256="sha256:" + "9" * 64,
        bundle_bytes=bundle_bytes,
    )
    promotion_path, _, _ = _write(tmp_path / "promotion.json", promotion)

    status = build_release_status(
        bundle_path=bundle_path,
        promotion_path=promotion_path,
        finalization_path=tmp_path / "finalization.json",
        current_version=CANDIDATE_VERSION,
    )

    assert status["phase"] == "invalid_release_chain"
    assert (
        "promotion bundle digest does not match the supplied bundle bytes"
        in status["blockers"]
    )
    assert status["gate"]["release_ready"] is False


def test_valid_full_chain_reports_finalized(tmp_path: Path) -> None:
    bundle = _bundle()
    bundle_path, bundle_digest, bundle_bytes = _write(
        tmp_path / "bundle.json",
        bundle,
    )
    promotion = _promotion(
        bundle_ref=str(bundle["bundle_ref"]),
        bundle_sha256=bundle_digest,
        bundle_bytes=bundle_bytes,
    )
    promotion_path, promotion_digest, promotion_bytes = _write(
        tmp_path / "promotion.json",
        promotion,
    )
    finalization = _finalization(
        promotion_ref=str(promotion["promotion_ref"]),
        promotion_sha256=promotion_digest,
        promotion_bytes=promotion_bytes,
    )
    finalization_path, _, _ = _write(
        tmp_path / "finalization.json",
        finalization,
    )

    status = build_release_status(
        bundle_path=bundle_path,
        promotion_path=promotion_path,
        finalization_path=finalization_path,
        current_version=RELEASE_VERSION,
    )

    assert status["phase"] == "finalized"
    assert status["blockers"] == []
    assert status["gate"] == {
        "release_ready": True,
        "release_finalized": True,
        "production_activation": False,
    }


def test_finalization_binding_detects_different_promotion_bytes(
    tmp_path: Path,
) -> None:
    bundle = _bundle()
    bundle_path, bundle_digest, bundle_bytes = _write(
        tmp_path / "bundle.json",
        bundle,
    )
    promotion = _promotion(
        bundle_ref=str(bundle["bundle_ref"]),
        bundle_sha256=bundle_digest,
        bundle_bytes=bundle_bytes,
    )
    promotion_path, _, promotion_bytes = _write(
        tmp_path / "promotion.json",
        promotion,
    )
    finalization = _finalization(
        promotion_ref=str(promotion["promotion_ref"]),
        promotion_sha256="sha256:" + "8" * 64,
        promotion_bytes=promotion_bytes,
    )
    finalization_path, _, _ = _write(
        tmp_path / "finalization.json",
        finalization,
    )

    status = build_release_status(
        bundle_path=bundle_path,
        promotion_path=promotion_path,
        finalization_path=finalization_path,
        current_version=RELEASE_VERSION,
    )

    assert status["phase"] == "invalid_release_chain"
    assert (
        "finalization promotion digest does not match the supplied promotion bytes"
        in status["blockers"]
    )
    assert status["gate"]["release_finalized"] is False
