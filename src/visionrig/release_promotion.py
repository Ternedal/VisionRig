from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx

from . import __version__
from .release_evidence import (
    BUNDLE_SCHEMA,
    ReleaseEvidenceError,
    validate_bundle_integrity,
)

PROMOTION_SCHEMA = "visionrig/v1-release-promotion/v1"
PROMOTION_PREFIX = "visionrig-release-promotion:"
OFFICIAL_REPOSITORY = "Ternedal/VisionRig"
REQUIRED_WORKFLOW_NAME = "tests"
REQUIRED_WORKFLOW_PATH = ".github/workflows/tests.yml"
REQUIRED_CI_JOBS = (
    "test",
    "kotlin-producer-core",
    "android-producer",
    "quest-producer",
)
DEFAULT_TARGET_VERSION = "1.0.0"
DEFAULT_OUTPUT = Path("validation/visionrig-v1-release-promotion.json")
MAX_BUNDLE_BYTES = 1024 * 1024


class ReleasePromotionError(RuntimeError):
    pass


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _read_json_object(path: Path, *, max_bytes: int) -> tuple[dict[str, Any], str, int]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ReleasePromotionError(f"unable to read release bundle: {path}") from exc
    if not raw:
        raise ReleasePromotionError("release bundle is empty")
    if len(raw) > max_bytes:
        raise ReleasePromotionError("release bundle exceeds bounded size")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleasePromotionError("release bundle is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ReleasePromotionError("release bundle must be a JSON object")
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    return value, digest, len(raw)


def validate_ci_run(
    run: Mapping[str, Any],
    jobs: Sequence[Mapping[str, Any]],
    *,
    expected_sha: str,
    repository: str = OFFICIAL_REPOSITORY,
) -> dict[str, Any]:
    if repository != OFFICIAL_REPOSITORY:
        raise ReleasePromotionError("release promotion repository is not official VisionRig")
    if run.get("name") != REQUIRED_WORKFLOW_NAME:
        raise ReleasePromotionError("CI workflow name mismatch")
    if run.get("path") != REQUIRED_WORKFLOW_PATH:
        raise ReleasePromotionError("CI workflow path mismatch")
    if run.get("event") != "push":
        raise ReleasePromotionError("release CI must be a push run")
    if run.get("head_branch") != "main":
        raise ReleasePromotionError("release CI must run on main")
    if run.get("head_sha") != expected_sha:
        raise ReleasePromotionError("release CI revision mismatch")
    if run.get("status") != "completed":
        raise ReleasePromotionError("release CI is not completed")
    if run.get("conclusion") != "success":
        raise ReleasePromotionError("release CI did not succeed")

    run_id = run.get("id")
    run_attempt = run.get("run_attempt")
    html_url = run.get("html_url")
    if not isinstance(run_id, int) or run_id <= 0:
        raise ReleasePromotionError("release CI run id is invalid")
    if not isinstance(run_attempt, int) or run_attempt <= 0:
        raise ReleasePromotionError("release CI run attempt is invalid")
    if not isinstance(html_url, str) or not html_url.startswith(
        "https://github.com/Ternedal/VisionRig/actions/runs/"
    ):
        raise ReleasePromotionError("release CI URL is invalid")

    by_name: dict[str, Mapping[str, Any]] = {}
    for job in jobs:
        name = job.get("name")
        if not isinstance(name, str) or not name:
            raise ReleasePromotionError("CI job name is invalid")
        if name in by_name:
            raise ReleasePromotionError("duplicate CI job name")
        by_name[name] = job

    summaries: list[dict[str, Any]] = []
    for name in REQUIRED_CI_JOBS:
        job = by_name.get(name)
        if job is None:
            raise ReleasePromotionError(f"required CI job is missing: {name}")
        if job.get("head_sha") != expected_sha:
            raise ReleasePromotionError(f"CI job revision mismatch: {name}")
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            raise ReleasePromotionError(f"required CI job is not green: {name}")
        job_id = job.get("id")
        if not isinstance(job_id, int) or job_id <= 0:
            raise ReleasePromotionError(f"CI job id is invalid: {name}")
        summaries.append(
            {
                "name": name,
                "job_id": job_id,
                "conclusion": "success",
            }
        )

    return {
        "repository": repository,
        "workflow_name": REQUIRED_WORKFLOW_NAME,
        "workflow_path": REQUIRED_WORKFLOW_PATH,
        "event": "push",
        "branch": "main",
        "run_id": run_id,
        "run_attempt": run_attempt,
        "run_url": html_url,
        "head_sha": expected_sha,
        "required_jobs": summaries,
    }


def _promotion_binding_payload(attestation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": attestation.get("schema"),
        "repository": attestation.get("repository"),
        "candidate_version": attestation.get("candidate_version"),
        "target_version": attestation.get("target_version"),
        "visionrig_git_sha": attestation.get("visionrig_git_sha"),
        "physical_evidence": attestation.get("physical_evidence"),
        "repository_ci": attestation.get("repository_ci"),
        "gate": attestation.get("gate"),
    }


def _expected_promotion_ref(attestation: Mapping[str, Any]) -> str:
    sha = attestation.get("visionrig_git_sha")
    if not isinstance(sha, str) or len(sha) != 40:
        raise ReleasePromotionError("promotion revision is malformed")
    digest = hashlib.sha256(
        _canonical_json(_promotion_binding_payload(attestation))
    ).hexdigest()
    return PROMOTION_PREFIX + sha + ":" + digest


def validate_promotion_attestation(attestation: Mapping[str, Any]) -> None:
    if attestation.get("schema") != PROMOTION_SCHEMA:
        raise ReleasePromotionError("promotion schema mismatch")
    if attestation.get("repository") != OFFICIAL_REPOSITORY:
        raise ReleasePromotionError("promotion repository mismatch")
    if attestation.get("target_version") != DEFAULT_TARGET_VERSION:
        raise ReleasePromotionError("promotion target version mismatch")

    physical = attestation.get("physical_evidence")
    if not isinstance(physical, Mapping):
        raise ReleasePromotionError("physical evidence binding is missing")
    bundle_ref = physical.get("bundle_ref")
    bundle_digest = physical.get("bundle_sha256")
    bundle_bytes = physical.get("bundle_bytes")
    if not isinstance(bundle_ref, str) or not bundle_ref.startswith(
        "visionrig-release-evidence:"
    ):
        raise ReleasePromotionError("physical bundle ref is invalid")
    if (
        not isinstance(bundle_digest, str)
        or not bundle_digest.startswith("sha256:")
        or len(bundle_digest) != 71
    ):
        raise ReleasePromotionError("physical bundle digest is invalid")
    if not isinstance(bundle_bytes, int) or not 1 <= bundle_bytes <= MAX_BUNDLE_BYTES:
        raise ReleasePromotionError("physical bundle byte count is invalid")

    ci = attestation.get("repository_ci")
    if not isinstance(ci, Mapping):
        raise ReleasePromotionError("repository CI binding is missing")
    if ci.get("head_sha") != attestation.get("visionrig_git_sha"):
        raise ReleasePromotionError("promotion CI revision mismatch")
    names = ci.get("required_jobs")
    if not isinstance(names, list):
        raise ReleasePromotionError("promotion CI jobs are missing")
    if [item.get("name") for item in names if isinstance(item, Mapping)] != list(
        REQUIRED_CI_JOBS
    ):
        raise ReleasePromotionError("promotion CI job set mismatch")

    expected_gate = {
        "physical_evidence_validated": True,
        "repository_ci_verified": True,
        "release_ready": True,
        "production_activation": False,
    }
    gate = attestation.get("gate")
    if not isinstance(gate, Mapping) or dict(gate) != expected_gate:
        raise ReleasePromotionError("promotion gate contract mismatch")

    actual_ref = attestation.get("promotion_ref")
    if not isinstance(actual_ref, str) or actual_ref != _expected_promotion_ref(
        attestation
    ):
        raise ReleasePromotionError("promotion ref hash mismatch")


def build_promotion_attestation(
    bundle_path: Path,
    *,
    expected_sha: str,
    expected_candidate_version: str,
    ci_run: Mapping[str, Any],
    ci_jobs: Sequence[Mapping[str, Any]],
    target_version: str = DEFAULT_TARGET_VERSION,
) -> dict[str, Any]:
    normalized_sha = expected_sha.strip().lower()
    if len(normalized_sha) != 40 or any(
        char not in "0123456789abcdef" for char in normalized_sha
    ):
        raise ReleasePromotionError("expected SHA must be 40 lowercase hex characters")
    if not expected_candidate_version.strip():
        raise ReleasePromotionError("candidate version must not be empty")
    if target_version != DEFAULT_TARGET_VERSION:
        raise ReleasePromotionError("v1 promotion target must be 1.0.0")

    bundle, bundle_digest, bundle_bytes = _read_json_object(
        bundle_path,
        max_bytes=MAX_BUNDLE_BYTES,
    )
    try:
        validate_bundle_integrity(bundle)
    except ReleaseEvidenceError as exc:
        raise ReleasePromotionError("physical release bundle integrity failed") from exc
    if bundle.get("schema") != BUNDLE_SCHEMA:
        raise ReleasePromotionError("physical release bundle schema mismatch")
    if bundle.get("visionrig_git_sha") != normalized_sha:
        raise ReleasePromotionError("physical release bundle revision mismatch")
    if bundle.get("visionrig_version") != expected_candidate_version:
        raise ReleasePromotionError("physical release bundle version mismatch")
    bundle_gate = bundle.get("gate")
    if not isinstance(bundle_gate, Mapping):
        raise ReleasePromotionError("physical release bundle gate is missing")
    if bundle_gate.get("physical_evidence_validated") is not True:
        raise ReleasePromotionError("physical release evidence is not validated")
    if bundle_gate.get("production_activation") is not False:
        raise ReleasePromotionError("physical bundle escalates production authority")

    ci_summary = validate_ci_run(
        ci_run,
        ci_jobs,
        expected_sha=normalized_sha,
    )

    attestation: dict[str, Any] = {
        "schema": PROMOTION_SCHEMA,
        "generated_at": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "repository": OFFICIAL_REPOSITORY,
        "candidate_version": expected_candidate_version,
        "target_version": target_version,
        "visionrig_git_sha": normalized_sha,
        "physical_evidence": {
            "bundle_ref": bundle.get("bundle_ref"),
            "bundle_sha256": bundle_digest,
            "bundle_bytes": bundle_bytes,
            "evidence_count": bundle.get("evidence_count"),
        },
        "repository_ci": ci_summary,
        "gate": {
            "physical_evidence_validated": True,
            "repository_ci_verified": True,
            "release_ready": True,
            "production_activation": False,
        },
    }
    attestation["promotion_ref"] = _expected_promotion_ref(attestation)
    validate_promotion_attestation(attestation)
    return attestation


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or destination.is_symlink():
        raise ReleasePromotionError("promotion output cannot be a symlink")
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=destination.name + ".",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, destination)
        temporary = None
    except OSError as exc:
        raise ReleasePromotionError("unable to write promotion attestation") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _github_json(client: httpx.Client, url: str) -> Any:
    response = client.get(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if response.status_code != 200:
        raise ReleasePromotionError(
            f"GitHub API request failed with HTTP {response.status_code}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ReleasePromotionError("GitHub API returned invalid JSON") from exc


def fetch_ci_evidence(
    run_id: int,
    *,
    token: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if run_id <= 0:
        raise ReleasePromotionError("CI run id must be positive")
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    with httpx.Client(timeout=20.0, headers=headers, follow_redirects=False) as client:
        base = f"https://api.github.com/repos/{OFFICIAL_REPOSITORY}/actions/runs/{run_id}"
        run = _github_json(client, base)
        jobs_payload = _github_json(client, base + "/jobs?per_page=100")
    if not isinstance(run, dict):
        raise ReleasePromotionError("GitHub CI run payload is invalid")
    if not isinstance(jobs_payload, dict):
        raise ReleasePromotionError("GitHub CI jobs payload is invalid")
    jobs = jobs_payload.get("jobs")
    if not isinstance(jobs, list) or not all(isinstance(item, dict) for item in jobs):
        raise ReleasePromotionError("GitHub CI jobs list is invalid")
    return run, jobs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify VisionRig physical evidence + exact green CI for v1 promotion"
    )
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--candidate-version", default=__version__)
    parser.add_argument("--target-version", default=DEFAULT_TARGET_VERSION)
    parser.add_argument("--ci-run-id", required=True, type=int)
    parser.add_argument("--github-token-env", default="GITHUB_TOKEN")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()

    token = os.getenv(args.github_token_env) if args.github_token_env else None
    try:
        run, jobs = fetch_ci_evidence(args.ci_run_id, token=token)
        attestation = build_promotion_attestation(
            args.bundle,
            expected_sha=args.expected_sha,
            expected_candidate_version=args.candidate_version,
            target_version=args.target_version,
            ci_run=run,
            ci_jobs=jobs,
        )
        _write_json_atomic(args.output, attestation)
    except ReleasePromotionError as exc:
        print("VisionRig v1 release promotion: FAIL: " + str(exc))
        return 1

    print(
        "VisionRig v1 release promotion: PASS "
        f"sha={attestation['visionrig_git_sha']} "
        f"candidate={attestation['candidate_version']} "
        f"target={attestation['target_version']} "
        f"ci_run={attestation['repository_ci']['run_id']} "
        f"promotion={attestation['promotion_ref']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
