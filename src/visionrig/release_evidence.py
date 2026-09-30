"""Validate and bundle privacy-safe physical release evidence.

This tool validates already-produced evidence. It never manufactures physical
acceptance and deliberately does not claim to verify repository CI status.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import __version__
from .kinect_acceptance import KinectPhysicalAcceptanceReceipt

BUNDLE_SCHEMA = "visionrig/release-evidence-bundle/v1"
PHYSICAL_SCHEMA = "visionrig/physical-perception-qualification/v2"
KINECT_SCHEMA = "visionrig/kinect-physical-acceptance/v3"
DEFAULT_OUTPUT = Path("validation/visionrig-release-evidence-bundle.json")
MAX_EVIDENCE_BYTES = 1024 * 1024
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_EVENT_REF = re.compile(r"^visionrig-event:[0-9a-f]{64}$")
_EVIDENCE_REF = re.compile(r"^world-evidence-event:[0-9a-f]{64}$")
_COGNITION_REF = re.compile(r"^cevt-[0-9a-f]{32}$")


class ReleaseEvidenceError(RuntimeError):
    """Release evidence is malformed, stale or not revision-bound."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _read_json(path: Path) -> Mapping[str, Any]:
    if path.is_symlink():
        raise ReleaseEvidenceError(f"evidence path cannot be a symlink: {path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ReleaseEvidenceError(f"evidence file is unavailable: {path}") from exc
    if size <= 0 or size > MAX_EVIDENCE_BYTES:
        raise ReleaseEvidenceError(
            f"evidence file size is outside (0, {MAX_EVIDENCE_BYTES}]: {path}"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseEvidenceError(f"evidence file is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(value, Mapping):
        raise ReleaseEvidenceError(f"evidence file must contain a JSON object: {path}")
    return value


def _require_mapping(
    value: object,
    *,
    field: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReleaseEvidenceError(f"{field} must be an object")
    return value


def _validate_generic_physical(
    report: Mapping[str, Any],
    *,
    expected_sha: str,
    expected_version: str,
) -> dict[str, Any]:
    if report.get("schema") != PHYSICAL_SCHEMA:
        raise ReleaseEvidenceError("physical qualification schema mismatch")

    visionrig = _require_mapping(report.get("visionrig"), field="visionrig")
    if visionrig.get("service_revision") != expected_sha:
        raise ReleaseEvidenceError(
            "physical qualification revision does not match expected SHA"
        )
    if visionrig.get("service_version") != expected_version:
        raise ReleaseEvidenceError(
            "physical qualification version does not match expected version"
        )
    if visionrig.get("perception_schema") != "visionrig/perception-event/v4":
        raise ReleaseEvidenceError("physical qualification perception schema mismatch")

    source = _require_mapping(report.get("physical_source"), field="physical_source")
    source_id = source.get("source_id")
    source_type = source.get("source_type")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ReleaseEvidenceError("physical qualification source id is missing")
    if source_type not in {"camera", "vr"}:
        raise ReleaseEvidenceError("physical qualification source type is not camera/vr")
    if source.get("presence") != "online":
        raise ReleaseEvidenceError("physical qualification source was not online")

    event = _require_mapping(report.get("event"), field="event")
    event_ref = event.get("visionrig_event_ref")
    if not isinstance(event_ref, str) or _EVENT_REF.fullmatch(event_ref) is None:
        raise ReleaseEvidenceError("physical qualification event ref is malformed")
    observation_count = event.get("semantic_observation_count")
    if not isinstance(observation_count, int) or observation_count < 1:
        raise ReleaseEvidenceError(
            "physical qualification has no semantic observations"
        )

    admission = _require_mapping(
        report.get("modelrig_admission"),
        field="modelrig_admission",
    )
    if admission.get("visionrig_event_ref") != event_ref:
        raise ReleaseEvidenceError(
            "ModelRig admission is not bound to the qualifying VisionRig event"
        )
    evidence_ref = admission.get("evidence_ref")
    if (
        not isinstance(evidence_ref, str)
        or _EVIDENCE_REF.fullmatch(evidence_ref) is None
    ):
        raise ReleaseEvidenceError("ModelRig evidence ref is malformed")
    cognition_ref = admission.get("cognition_event_id")
    if (
        not isinstance(cognition_ref, str)
        or _COGNITION_REF.fullmatch(cognition_ref) is None
    ):
        raise ReleaseEvidenceError("ModelRig cognition event id is malformed")
    for field, expected in (
        ("world_changed", True),
        ("replayed", False),
        ("cognition_event_queued", True),
        ("model_calls", 0),
    ):
        if admission.get(field) != expected:
            raise ReleaseEvidenceError(
                f"ModelRig admission field {field} does not prove release evidence"
            )

    privacy = _require_mapping(report.get("privacy"), field="privacy")
    for field in (
        "raw_frame_included",
        "ocr_text_included",
        "landmarks_included",
        "semantic_payload_included",
    ):
        if privacy.get(field) is not False:
            raise ReleaseEvidenceError(
                f"physical qualification privacy field {field} must be false"
            )

    gate = _require_mapping(report.get("gate"), field="gate")
    for field in ("passed", "physical_perception_qualified"):
        if gate.get(field) is not True:
            raise ReleaseEvidenceError(f"physical qualification gate {field} is not true")
    for field in (
        "raw_sensor_authority_granted",
        "identity_authority_granted",
        "production_activation",
    ):
        if gate.get(field) is not False:
            raise ReleaseEvidenceError(
                f"physical qualification gate {field} must remain false"
            )

    actual_ref = report.get("release_evidence_ref")
    if not isinstance(actual_ref, str):
        raise ReleaseEvidenceError(
            "physical qualification lacks canonical release_evidence_ref"
        )
    unsigned = dict(report)
    unsigned.pop("release_evidence_ref", None)
    expected_ref = (
        "visionrig-physical-perception:"
        + expected_sha
        + ":"
        + hashlib.sha256(_canonical_json(unsigned)).hexdigest()
    )
    if actual_ref != expected_ref:
        raise ReleaseEvidenceError(
            "physical qualification release_evidence_ref hash mismatch"
        )

    return {
        "schema": PHYSICAL_SCHEMA,
        "source_id": source_id,
        "source_type": source_type,
        "device": source.get("device"),
        "revision": expected_sha,
        "version": expected_version,
        "release_evidence_ref": actual_ref,
    }


def _validate_kinect(
    report: Mapping[str, Any],
    *,
    expected_sha: str,
    expected_version: str,
) -> dict[str, Any]:
    try:
        receipt = KinectPhysicalAcceptanceReceipt.model_validate_json(
            json.dumps(
                report,
                ensure_ascii=True,
                separators=(",", ":"),
            )
        )
    except Exception as exc:
        raise ReleaseEvidenceError(
            "Kinect physical acceptance receipt is invalid"
        ) from exc

    if receipt.visionrig_git_sha != expected_sha:
        raise ReleaseEvidenceError(
            "Kinect physical acceptance revision does not match expected SHA"
        )
    if receipt.visionrig_version != expected_version:
        raise ReleaseEvidenceError(
            "Kinect physical acceptance version does not match expected version"
        )

    for ref in receipt.modelrig_event_refs:
        if _EVENT_REF.fullmatch(ref) is None:
            raise ReleaseEvidenceError("Kinect ModelRig event ref is malformed")
    for ref in receipt.modelrig_evidence_refs:
        if _EVIDENCE_REF.fullmatch(ref) is None:
            raise ReleaseEvidenceError("Kinect ModelRig evidence ref is malformed")
    for ref in receipt.modelrig_cognition_event_ids:
        if _COGNITION_REF.fullmatch(ref) is None:
            raise ReleaseEvidenceError("Kinect cognition event id is malformed")

    return {
        "schema": KINECT_SCHEMA,
        "source_id": receipt.source_id,
        "source_type": "camera",
        "device": receipt.device,
        "revision": receipt.visionrig_git_sha,
        "version": receipt.visionrig_version,
        "captured_frames": receipt.captured_frames,
    }


def validate_evidence(
    report: Mapping[str, Any],
    *,
    expected_sha: str,
    expected_version: str,
) -> dict[str, Any]:
    schema = report.get("schema")
    if schema == PHYSICAL_SCHEMA:
        return _validate_generic_physical(
            report,
            expected_sha=expected_sha,
            expected_version=expected_version,
        )
    if schema == KINECT_SCHEMA:
        return _validate_kinect(
            report,
            expected_sha=expected_sha,
            expected_version=expected_version,
        )
    raise ReleaseEvidenceError(f"unsupported release evidence schema: {schema!r}")


def _git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseEvidenceError("git checkout identity unavailable") from exc
    if result.returncode != 0:
        raise ReleaseEvidenceError("git checkout identity unavailable")
    return result.stdout.strip()


def require_exact_clean_checkout(expected_sha: str) -> None:
    if _SHA40.fullmatch(expected_sha) is None:
        raise ReleaseEvidenceError("expected SHA must be lowercase 40-hex")
    if _git("rev-parse", "HEAD") != expected_sha:
        raise ReleaseEvidenceError("checkout HEAD does not match expected SHA")
    if _git("status", "--porcelain"):
        raise ReleaseEvidenceError("release evidence requires an exact clean checkout")


def build_release_evidence_bundle(
    evidence_paths: Sequence[Path],
    *,
    expected_sha: str,
    expected_version: str = __version__,
    require_clean_checkout: bool = True,
) -> dict[str, Any]:
    normalized_sha = expected_sha.strip().lower()
    if _SHA40.fullmatch(normalized_sha) is None:
        raise ReleaseEvidenceError("expected SHA must be lowercase 40-hex")
    if not expected_version.strip():
        raise ReleaseEvidenceError("expected version must not be empty")
    if not 1 <= len(evidence_paths) <= 16:
        raise ReleaseEvidenceError("release evidence requires between 1 and 16 receipts")
    if require_clean_checkout:
        require_exact_clean_checkout(normalized_sha)

    evidence: list[dict[str, Any]] = []
    source_keys: set[tuple[str, str]] = set()
    for path in evidence_paths:
        summary = validate_evidence(
            _read_json(path),
            expected_sha=normalized_sha,
            expected_version=expected_version,
        )
        key = (str(summary["source_type"]), str(summary["source_id"]))
        if key in source_keys:
            raise ReleaseEvidenceError(
                "duplicate physical evidence for source " + summary["source_id"]
            )
        source_keys.add(key)
        evidence.append({"path": str(path), **summary})

    evidence.sort(key=lambda item: (str(item["source_type"]), str(item["source_id"])))
    return {
        "schema": BUNDLE_SCHEMA,
        "generated_at": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "visionrig_version": expected_version,
        "visionrig_git_sha": normalized_sha,
        "evidence_count": len(evidence),
        "evidence": evidence,
        "gate": {
            "physical_evidence_validated": True,
            "repository_ci_required": True,
            "repository_ci_verified_by_this_tool": False,
            "production_activation": False,
        },
    }


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or destination.is_symlink():
        raise ReleaseEvidenceError("bundle output cannot be a symlink")
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate revision-bound VisionRig physical release evidence"
    )
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--expected-version", default=__version__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("evidence", type=Path, nargs="+")
    args = parser.parse_args()

    try:
        bundle = build_release_evidence_bundle(
            args.evidence,
            expected_sha=args.expected_sha,
            expected_version=args.expected_version,
        )
        _write_json_atomic(args.output, bundle)
    except ReleaseEvidenceError as exc:
        print("VisionRig release evidence: FAIL: " + str(exc))
        return 1

    print(
        "VisionRig release evidence: PASS "
        f"receipts={bundle['evidence_count']} "
        f"sha={bundle['visionrig_git_sha']} "
        f"version={bundle['visionrig_version']} "
        f"bundle={args.output} "
        "ci_verified=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
