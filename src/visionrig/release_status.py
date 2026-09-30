from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from . import __version__
from .release_evidence import (
    DEFAULT_OUTPUT as DEFAULT_BUNDLE_PATH,
    ReleaseEvidenceError,
    validate_bundle_integrity,
)
from .release_finalization import (
    DEFAULT_OUTPUT as DEFAULT_FINALIZATION_PATH,
    ReleaseFinalizationError,
    validate_finalization_attestation,
)
from .release_promotion import (
    DEFAULT_CANDIDATE_VERSION,
    DEFAULT_OUTPUT as DEFAULT_PROMOTION_PATH,
    DEFAULT_TARGET_VERSION,
    ReleasePromotionError,
    validate_promotion_attestation,
)

STATUS_SCHEMA = "visionrig/v1-release-status/v1"
MAX_ARTIFACT_BYTES = 1024 * 1024


class ReleaseStatusError(RuntimeError):
    pass


def _read_json_artifact(
    path: Path,
    *,
    max_bytes: int = MAX_ARTIFACT_BYTES,
) -> tuple[dict[str, Any], str, int]:
    if path.is_symlink():
        raise ReleaseStatusError(f"release artifact cannot be a symlink: {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ReleaseStatusError(f"unable to read release artifact: {path}") from exc
    if not raw:
        raise ReleaseStatusError(f"release artifact is empty: {path}")
    if len(raw) > max_bytes:
        raise ReleaseStatusError(f"release artifact exceeds bounded size: {path}")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseStatusError(f"release artifact is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ReleaseStatusError(f"release artifact must be a JSON object: {path}")
    return value, "sha256:" + hashlib.sha256(raw).hexdigest(), len(raw)


def _inspect_artifact(
    path: Path,
    *,
    validator: Callable[[Mapping[str, Any]], None],
    error_types: tuple[type[Exception], ...],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not path.exists():
        return (
            {
                "path": str(path),
                "state": "missing",
            },
            None,
        )

    try:
        value, digest, size = _read_json_artifact(path)
        validator(value)
    except ReleaseStatusError as exc:
        return (
            {
                "path": str(path),
                "state": "invalid",
                "error": str(exc),
            },
            None,
        )
    except error_types as exc:
        return (
            {
                "path": str(path),
                "state": "invalid",
                "error": str(exc),
            },
            None,
        )

    return (
        {
            "path": str(path),
            "state": "valid",
            "sha256": digest,
            "bytes": size,
        },
        value,
    )


def _append_blocker(blockers: list[str], message: str) -> None:
    if message not in blockers:
        blockers.append(message)


def _validate_bundle_to_promotion_binding(
    *,
    bundle_meta: Mapping[str, Any],
    bundle: Mapping[str, Any],
    promotion: Mapping[str, Any],
    blockers: list[str],
) -> None:
    physical = promotion.get("physical_evidence")
    if not isinstance(physical, Mapping):
        _append_blocker(blockers, "promotion physical evidence binding is missing")
        return

    checks = (
        (
            physical.get("bundle_ref"),
            bundle.get("bundle_ref"),
            "promotion bundle_ref does not match the supplied bundle",
        ),
        (
            physical.get("bundle_sha256"),
            bundle_meta.get("sha256"),
            "promotion bundle digest does not match the supplied bundle bytes",
        ),
        (
            physical.get("bundle_bytes"),
            bundle_meta.get("bytes"),
            "promotion bundle byte count does not match the supplied bundle",
        ),
        (
            physical.get("evidence_count"),
            bundle.get("evidence_count"),
            "promotion evidence count does not match the supplied bundle",
        ),
        (
            promotion.get("visionrig_git_sha"),
            bundle.get("visionrig_git_sha"),
            "promotion revision does not match the supplied bundle",
        ),
        (
            promotion.get("candidate_version"),
            bundle.get("visionrig_version"),
            "promotion candidate version does not match the supplied bundle",
        ),
    )
    for actual, expected, message in checks:
        if actual != expected:
            _append_blocker(blockers, message)


def _validate_promotion_to_finalization_binding(
    *,
    promotion_meta: Mapping[str, Any],
    promotion: Mapping[str, Any],
    finalization: Mapping[str, Any],
    blockers: list[str],
) -> None:
    bound = finalization.get("promotion")
    if not isinstance(bound, Mapping):
        _append_blocker(blockers, "finalization promotion binding is missing")
        return

    checks = (
        (
            bound.get("promotion_ref"),
            promotion.get("promotion_ref"),
            "finalization promotion_ref does not match the supplied promotion",
        ),
        (
            bound.get("promotion_sha256"),
            promotion_meta.get("sha256"),
            "finalization promotion digest does not match the supplied promotion bytes",
        ),
        (
            bound.get("promotion_bytes"),
            promotion_meta.get("bytes"),
            "finalization promotion byte count does not match the supplied promotion",
        ),
        (
            finalization.get("candidate_sha"),
            promotion.get("visionrig_git_sha"),
            "finalization candidate revision does not match the supplied promotion",
        ),
        (
            finalization.get("candidate_version"),
            promotion.get("candidate_version"),
            "finalization candidate version does not match the supplied promotion",
        ),
        (
            finalization.get("release_version"),
            promotion.get("target_version"),
            "finalization release version does not match the promotion target",
        ),
    )
    for actual, expected, message in checks:
        if actual != expected:
            _append_blocker(blockers, message)


def build_release_status(
    *,
    bundle_path: Path = DEFAULT_BUNDLE_PATH,
    promotion_path: Path = DEFAULT_PROMOTION_PATH,
    finalization_path: Path = DEFAULT_FINALIZATION_PATH,
    current_version: str = __version__,
) -> dict[str, Any]:
    blockers: list[str] = []

    bundle_meta, bundle = _inspect_artifact(
        bundle_path,
        validator=validate_bundle_integrity,
        error_types=(ReleaseEvidenceError,),
    )
    promotion_meta, promotion = _inspect_artifact(
        promotion_path,
        validator=validate_promotion_attestation,
        error_types=(ReleasePromotionError,),
    )
    finalization_meta, finalization = _inspect_artifact(
        finalization_path,
        validator=validate_finalization_attestation,
        error_types=(ReleaseFinalizationError,),
    )

    for label, meta in (
        ("physical evidence bundle", bundle_meta),
        ("promotion attestation", promotion_meta),
        ("finalization attestation", finalization_meta),
    ):
        if meta["state"] == "invalid":
            _append_blocker(
                blockers,
                label + " is invalid: " + str(meta.get("error", "unknown error")),
            )

    if promotion is not None:
        if bundle is None:
            _append_blocker(
                blockers,
                "promotion exists but the physical evidence bundle is unavailable or invalid",
            )
        else:
            _validate_bundle_to_promotion_binding(
                bundle_meta=bundle_meta,
                bundle=bundle,
                promotion=promotion,
                blockers=blockers,
            )

    if finalization is not None:
        if promotion is None:
            _append_blocker(
                blockers,
                "finalization exists but the promotion attestation is unavailable or invalid",
            )
        else:
            _validate_promotion_to_finalization_binding(
                promotion_meta=promotion_meta,
                promotion=promotion,
                finalization=finalization,
                blockers=blockers,
            )

    if bundle is not None and bundle.get("visionrig_version") != DEFAULT_CANDIDATE_VERSION:
        _append_blocker(
            blockers,
            "physical evidence bundle is not for the 0.99.0 v1 candidate",
        )

    artifact_states = {
        "physical_evidence": bundle_meta,
        "promotion": promotion_meta,
        "finalization": finalization_meta,
    }

    chain_valid = not blockers
    release_ready = promotion is not None and chain_valid
    release_finalized = finalization is not None and chain_valid

    if finalization is not None and chain_valid:
        if current_version != DEFAULT_TARGET_VERSION:
            _append_blocker(
                blockers,
                "finalization is valid but the installed VisionRig version is not 1.0.0",
            )
            phase = "release_version_mismatch"
        else:
            phase = "finalized"
    elif promotion is not None and chain_valid:
        if current_version != DEFAULT_CANDIDATE_VERSION:
            _append_blocker(
                blockers,
                "promotion is valid but the installed VisionRig version is not 0.99.0",
            )
            phase = "candidate_version_mismatch"
        else:
            phase = "version_bump_pending"
    elif bundle is not None and chain_valid:
        if current_version != DEFAULT_CANDIDATE_VERSION:
            _append_blocker(
                blockers,
                "physical evidence is valid but the installed VisionRig version is not 0.99.0",
            )
            phase = "candidate_version_mismatch"
        else:
            _append_blocker(
                blockers,
                "promotion attestation has not been produced yet",
            )
            phase = "promotion_pending"
    elif chain_valid:
        _append_blocker(
            blockers,
            "revision-bound physical release evidence has not been bundled yet",
        )
        phase = "physical_evidence_pending"
    else:
        phase = "invalid_release_chain"

    if phase != "finalized":
        release_finalized = False
    if phase not in {"version_bump_pending", "finalized"}:
        release_ready = False

    return {
        "schema": STATUS_SCHEMA,
        "visionrig_version": current_version,
        "candidate_version": DEFAULT_CANDIDATE_VERSION,
        "target_version": DEFAULT_TARGET_VERSION,
        "phase": phase,
        "artifacts": artifact_states,
        "blockers": blockers,
        "gate": {
            "release_ready": release_ready,
            "release_finalized": release_finalized,
            "production_activation": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect VisionRig v1 release artifacts without manufacturing evidence"
    )
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE_PATH)
    parser.add_argument("--promotion", type=Path, default=DEFAULT_PROMOTION_PATH)
    parser.add_argument("--finalization", type=Path, default=DEFAULT_FINALIZATION_PATH)
    parser.add_argument("--json", action="store_true", dest="as_json")
    requirement = parser.add_mutually_exclusive_group()
    requirement.add_argument("--require-ready", action="store_true")
    requirement.add_argument("--require-finalized", action="store_true")
    args = parser.parse_args()

    status = build_release_status(
        bundle_path=args.bundle,
        promotion_path=args.promotion,
        finalization_path=args.finalization,
    )

    if args.as_json:
        print(json.dumps(status, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(
            "VisionRig release status: "
            + str(status["phase"])
            + " version="
            + str(status["visionrig_version"])
        )
        for name, artifact in status["artifacts"].items():
            print(
                "  "
                + name
                + ": "
                + str(artifact["state"])
                + " "
                + str(artifact["path"])
            )
        for blocker in status["blockers"]:
            print("  blocker: " + str(blocker))

    if status["phase"] == "invalid_release_chain":
        return 1
    if args.require_finalized and status["gate"]["release_finalized"] is not True:
        return 1
    if args.require_ready and status["gate"]["release_ready"] is not True:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
