from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .release_promotion import (
    DEFAULT_TARGET_VERSION,
    OFFICIAL_REPOSITORY,
    ReleasePromotionError,
    fetch_ci_evidence,
    validate_ci_run,
    validate_ci_summary,
    validate_promotion_attestation,
)

FINALIZATION_SCHEMA = "visionrig/v1-release-finalization/v1"
FINALIZATION_PREFIX = "visionrig-release-finalization:"
DEFAULT_OUTPUT = Path("validation/visionrig-v1-release-finalization.json")
MAX_PROMOTION_BYTES = 1024 * 1024

CANDIDATE_VERSION = "0.99.0"
RELEASE_VERSION = DEFAULT_TARGET_VERSION

REQUIRED_VERSION_PATHS = (
    "pyproject.toml",
    "src/visionrig/__init__.py",
    "tests/test_version.py",
)
ALLOWED_RELEASE_PATHS = frozenset(
    {
        *REQUIRED_VERSION_PATHS,
        "README.md",
        "docs/ARCHITECTURE.md",
        "docs/RELEASE.md",
    }
)


class ReleaseFinalizationError(RuntimeError):
    pass


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _git(*args: str, preserve_output: bool = False) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReleaseFinalizationError(
            "unable to inspect release checkout with git"
        ) from exc
    return result.stdout if preserve_output else result.stdout.strip()


def _read_json_object(
    path: Path,
    *,
    max_bytes: int,
) -> tuple[dict[str, Any], str, int]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ReleaseFinalizationError(
            f"unable to read promotion attestation: {path}"
        ) from exc
    if not raw:
        raise ReleaseFinalizationError("promotion attestation is empty")
    if len(raw) > max_bytes:
        raise ReleaseFinalizationError(
            "promotion attestation exceeds bounded size"
        )
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseFinalizationError(
            "promotion attestation is not valid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ReleaseFinalizationError(
            "promotion attestation must be a JSON object"
        )
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    return value, digest, len(raw)


def validate_release_changed_paths(changed_paths: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(sorted(set(changed_paths)))
    if not normalized:
        raise ReleaseFinalizationError("release commit changes no files")
    unexpected = [path for path in normalized if path not in ALLOWED_RELEASE_PATHS]
    if unexpected:
        raise ReleaseFinalizationError(
            "release commit changes non-release files: " + ", ".join(unexpected)
        )
    missing = [path for path in REQUIRED_VERSION_PATHS if path not in normalized]
    if missing:
        raise ReleaseFinalizationError(
            "release commit lacks required version files: " + ", ".join(missing)
        )
    return normalized


def _require_exact_text_transition(
    *,
    path: str,
    candidate_text: str,
    release_text: str,
    replacements: Sequence[tuple[str, str]],
) -> None:
    expected = candidate_text
    for old, new in replacements:
        if old not in expected:
            raise ReleaseFinalizationError(
                f"candidate version marker missing in {path}"
            )
        expected = expected.replace(old, new, 1)
    if expected != release_text:
        raise ReleaseFinalizationError(
            f"release commit changes more than version contract in {path}"
        )


def validate_version_file_transition(
    candidate_files: Mapping[str, str],
    release_files: Mapping[str, str],
) -> None:
    for path in REQUIRED_VERSION_PATHS:
        if path not in candidate_files or path not in release_files:
            raise ReleaseFinalizationError(
                f"release version file is missing: {path}"
            )

    _require_exact_text_transition(
        path="pyproject.toml",
        candidate_text=candidate_files["pyproject.toml"],
        release_text=release_files["pyproject.toml"],
        replacements=(
            (
                'version = "0.99.0"',
                'version = "1.0.0"',
            ),
        ),
    )
    _require_exact_text_transition(
        path="src/visionrig/__init__.py",
        candidate_text=candidate_files["src/visionrig/__init__.py"],
        release_text=release_files["src/visionrig/__init__.py"],
        replacements=(
            (
                '__version__ = "0.99.0"',
                '__version__ = "1.0.0"',
            ),
        ),
    )
    _require_exact_text_transition(
        path="tests/test_version.py",
        candidate_text=candidate_files["tests/test_version.py"],
        release_text=release_files["tests/test_version.py"],
        replacements=(
            (
                "def test_release_candidate_version() -> None:",
                "def test_v1_release_version() -> None:",
            ),
            (
                'assert __version__ == "0.99.0"',
                'assert __version__ == "1.0.0"',
            ),
        ),
    )


def inspect_release_checkout(candidate_sha: str) -> dict[str, Any]:
    normalized_candidate = candidate_sha.strip().lower()
    if len(normalized_candidate) != 40 or any(
        char not in "0123456789abcdef" for char in normalized_candidate
    ):
        raise ReleaseFinalizationError(
            "candidate SHA must be 40 lowercase hex characters"
        )

    if _git("status", "--porcelain"):
        raise ReleaseFinalizationError("release checkout is not clean")

    release_sha = _git("rev-parse", "HEAD").lower()
    parent_line = _git("rev-list", "--parents", "-n", "1", "HEAD").split()
    if len(parent_line) != 2:
        raise ReleaseFinalizationError(
            "release commit must have exactly one parent"
        )
    if parent_line[0].lower() != release_sha:
        raise ReleaseFinalizationError("release HEAD identity mismatch")
    if parent_line[1].lower() != normalized_candidate:
        raise ReleaseFinalizationError(
            "release commit parent is not the promoted candidate"
        )

    changed = [
        line.strip()
        for line in _git(
            "diff",
            "--name-only",
            normalized_candidate,
            release_sha,
        ).splitlines()
        if line.strip()
    ]
    changed_paths = validate_release_changed_paths(changed)

    candidate_files: dict[str, str] = {}
    release_files: dict[str, str] = {}
    for path in REQUIRED_VERSION_PATHS:
        candidate_files[path] = _git(
            "show",
            normalized_candidate + ":" + path,
            preserve_output=True,
        )
        try:
            release_files[path] = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise ReleaseFinalizationError(
                f"unable to read release version file: {path}"
            ) from exc
    validate_version_file_transition(candidate_files, release_files)

    return {
        "candidate_sha": normalized_candidate,
        "release_sha": release_sha,
        "changed_paths": list(changed_paths),
        "candidate_version": CANDIDATE_VERSION,
        "release_version": RELEASE_VERSION,
    }


def _finalization_binding_payload(
    attestation: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": attestation.get("schema"),
        "repository": attestation.get("repository"),
        "candidate_version": attestation.get("candidate_version"),
        "release_version": attestation.get("release_version"),
        "candidate_sha": attestation.get("candidate_sha"),
        "release_sha": attestation.get("release_sha"),
        "promotion": attestation.get("promotion"),
        "release_delta": attestation.get("release_delta"),
        "repository_ci": attestation.get("repository_ci"),
        "gate": attestation.get("gate"),
    }


def _expected_finalization_ref(attestation: Mapping[str, Any]) -> str:
    release_sha = attestation.get("release_sha")
    if not isinstance(release_sha, str) or len(release_sha) != 40:
        raise ReleaseFinalizationError("release revision is malformed")
    digest = hashlib.sha256(
        _canonical_json(_finalization_binding_payload(attestation))
    ).hexdigest()
    return FINALIZATION_PREFIX + release_sha + ":" + digest


def validate_finalization_attestation(
    attestation: Mapping[str, Any],
) -> None:
    if attestation.get("schema") != FINALIZATION_SCHEMA:
        raise ReleaseFinalizationError("finalization schema mismatch")
    if attestation.get("repository") != OFFICIAL_REPOSITORY:
        raise ReleaseFinalizationError("finalization repository mismatch")
    if attestation.get("candidate_version") != CANDIDATE_VERSION:
        raise ReleaseFinalizationError("candidate version mismatch")
    if attestation.get("release_version") != RELEASE_VERSION:
        raise ReleaseFinalizationError("release version mismatch")

    candidate_sha = attestation.get("candidate_sha")
    release_sha = attestation.get("release_sha")
    if not isinstance(candidate_sha, str) or len(candidate_sha) != 40:
        raise ReleaseFinalizationError("candidate revision is malformed")
    if not isinstance(release_sha, str) or len(release_sha) != 40:
        raise ReleaseFinalizationError("release revision is malformed")
    if candidate_sha == release_sha:
        raise ReleaseFinalizationError(
            "release revision must differ from candidate revision"
        )

    promotion = attestation.get("promotion")
    if not isinstance(promotion, Mapping):
        raise ReleaseFinalizationError("promotion binding is missing")
    promotion_ref = promotion.get("promotion_ref")
    if (
        not isinstance(promotion_ref, str)
        or not promotion_ref.startswith(
            "visionrig-release-promotion:" + candidate_sha + ":"
        )
    ):
        raise ReleaseFinalizationError(
            "promotion ref is not bound to candidate revision"
        )
    promotion_digest = promotion.get("promotion_sha256")
    if (
        not isinstance(promotion_digest, str)
        or not promotion_digest.startswith("sha256:")
        or len(promotion_digest) != 71
    ):
        raise ReleaseFinalizationError("promotion digest is invalid")
    promotion_bytes = promotion.get("promotion_bytes")
    if (
        not isinstance(promotion_bytes, int)
        or not 1 <= promotion_bytes <= MAX_PROMOTION_BYTES
    ):
        raise ReleaseFinalizationError("promotion byte count is invalid")

    delta = attestation.get("release_delta")
    if not isinstance(delta, Mapping):
        raise ReleaseFinalizationError("release delta is missing")
    changed_paths = delta.get("changed_paths")
    if not isinstance(changed_paths, list) or not all(
        isinstance(path, str) for path in changed_paths
    ):
        raise ReleaseFinalizationError("release changed paths are invalid")
    if list(validate_release_changed_paths(changed_paths)) != changed_paths:
        raise ReleaseFinalizationError(
            "release changed paths are not canonical"
        )

    ci = attestation.get("repository_ci")
    if not isinstance(ci, Mapping):
        raise ReleaseFinalizationError("release CI binding is missing")
    try:
        validate_ci_summary(ci, expected_sha=release_sha)
    except ReleasePromotionError as exc:
        raise ReleaseFinalizationError(
            "release CI summary is invalid"
        ) from exc

    expected_gate = {
        "candidate_promotion_verified": True,
        "version_only_release_delta": True,
        "repository_ci_verified": True,
        "release_finalized": True,
        "production_activation": False,
    }
    gate = attestation.get("gate")
    if not isinstance(gate, Mapping) or dict(gate) != expected_gate:
        raise ReleaseFinalizationError("finalization gate contract mismatch")

    actual_ref = attestation.get("finalization_ref")
    if (
        not isinstance(actual_ref, str)
        or actual_ref != _expected_finalization_ref(attestation)
    ):
        raise ReleaseFinalizationError("finalization ref hash mismatch")


def build_finalization_attestation(
    promotion_path: Path,
    *,
    checkout: Mapping[str, Any],
    ci_run: Mapping[str, Any],
    ci_jobs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    promotion, promotion_digest, promotion_bytes = _read_json_object(
        promotion_path,
        max_bytes=MAX_PROMOTION_BYTES,
    )
    try:
        validate_promotion_attestation(promotion)
    except ReleasePromotionError as exc:
        raise ReleaseFinalizationError(
            "candidate promotion attestation is invalid"
        ) from exc

    candidate_sha = checkout.get("candidate_sha")
    release_sha = checkout.get("release_sha")
    changed_paths = checkout.get("changed_paths")
    if promotion.get("visionrig_git_sha") != candidate_sha:
        raise ReleaseFinalizationError(
            "promotion candidate does not match release parent"
        )
    if promotion.get("candidate_version") != CANDIDATE_VERSION:
        raise ReleaseFinalizationError(
            "promotion candidate version mismatch"
        )
    if promotion.get("target_version") != RELEASE_VERSION:
        raise ReleaseFinalizationError(
            "promotion target version mismatch"
        )
    if not isinstance(release_sha, str):
        raise ReleaseFinalizationError("release checkout lacks release SHA")
    if not isinstance(changed_paths, list):
        raise ReleaseFinalizationError(
            "release checkout lacks changed paths"
        )
    canonical_paths = list(validate_release_changed_paths(changed_paths))

    try:
        ci_summary = validate_ci_run(
            ci_run,
            ci_jobs,
            expected_sha=release_sha,
        )
    except ReleasePromotionError as exc:
        raise ReleaseFinalizationError(
            "release commit CI verification failed"
        ) from exc

    attestation: dict[str, Any] = {
        "schema": FINALIZATION_SCHEMA,
        "generated_at": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "repository": OFFICIAL_REPOSITORY,
        "candidate_version": CANDIDATE_VERSION,
        "release_version": RELEASE_VERSION,
        "candidate_sha": candidate_sha,
        "release_sha": release_sha,
        "promotion": {
            "promotion_ref": promotion.get("promotion_ref"),
            "promotion_sha256": promotion_digest,
            "promotion_bytes": promotion_bytes,
        },
        "release_delta": {
            "changed_paths": canonical_paths,
        },
        "repository_ci": ci_summary,
        "gate": {
            "candidate_promotion_verified": True,
            "version_only_release_delta": True,
            "repository_ci_verified": True,
            "release_finalized": True,
            "production_activation": False,
        },
    }
    attestation["finalization_ref"] = _expected_finalization_ref(attestation)
    validate_finalization_attestation(attestation)
    return attestation


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or destination.is_symlink():
        raise ReleaseFinalizationError(
            "finalization output cannot be a symlink"
        )
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )
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
        raise ReleaseFinalizationError(
            "unable to write finalization attestation"
        ) from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the one-commit VisionRig 0.99.0 -> 1.0.0 finalization"
        )
    )
    parser.add_argument("--ci-run-id", required=True, type=int)
    parser.add_argument("--github-token-env", default="GITHUB_TOKEN")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("promotion", type=Path)
    args = parser.parse_args()

    try:
        promotion, _, _ = _read_json_object(
            args.promotion,
            max_bytes=MAX_PROMOTION_BYTES,
        )
        try:
            validate_promotion_attestation(promotion)
        except ReleasePromotionError as exc:
            raise ReleaseFinalizationError(
                "candidate promotion attestation is invalid"
            ) from exc
        candidate_sha = promotion.get("visionrig_git_sha")
        if not isinstance(candidate_sha, str):
            raise ReleaseFinalizationError(
                "promotion candidate revision is missing"
            )

        checkout = inspect_release_checkout(candidate_sha)
        token = (
            os.getenv(args.github_token_env)
            if args.github_token_env
            else None
        )
        run, jobs = fetch_ci_evidence(args.ci_run_id, token=token)
        attestation = build_finalization_attestation(
            args.promotion,
            checkout=checkout,
            ci_run=run,
            ci_jobs=jobs,
        )
        _write_json_atomic(args.output, attestation)
    except ReleaseFinalizationError as exc:
        print("VisionRig v1 release finalization: FAIL: " + str(exc))
        return 1

    print(
        "VisionRig v1 release finalization: PASS "
        f"candidate={attestation['candidate_sha']} "
        f"release={attestation['release_sha']} "
        f"version={attestation['release_version']} "
        f"ci_run={attestation['repository_ci']['run_id']} "
        f"finalization={attestation['finalization_ref']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
