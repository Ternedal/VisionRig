"""Physical VisionRig -> ModelRig perception qualification.

The qualifier observes the running VisionRig service over loopback. It never
uploads or persists raw frames. A PASS requires:
- one online physical camera/VR source already producing frames;
- one fresh PerceptionEvent/v3 from that exact source after probe start;
- VisionRig's ModelRig bridge enabled and bound to loopback;
- a verified bridge result for that exact source/frame;
- the ModelRig receipt bound to the canonical SHA-256 event ref;
- inferred epistemics, zero model calls, and no execution/scheduling/memory or
  production authority.

The evidence report stores only identity/provenance/latency facts, never the
PerceptionEvent semantic payload.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

SCHEMA = "visionrig/physical-perception-qualification/v1"
DEFAULT_VISIONRIG_URL = "http://127.0.0.1:8110"
DEFAULT_REPORT = Path("validation/visionrig-physical-perception-latest.json")
MAX_RESPONSE_BYTES = 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 60.0
POLL_SECONDS = 0.25
PHYSICAL_SOURCE_TYPES = {"camera", "vr"}
_SHA_REF = re.compile(r"^visionrig-event:[a-f0-9]{64}$")


class PhysicalPerceptionQualificationError(RuntimeError):
    """Physical perception evidence could not be established truthfully."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _event_ref(event: Mapping[str, Any]) -> str:
    return "visionrig-event:" + hashlib.sha256(_canonical_json(event)).hexdigest()


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(path)


def _loopback_origin(value: str) -> str:
    raw = value.strip().rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(raw)
        host = parsed.hostname
    except ValueError as exc:
        raise PhysicalPerceptionQualificationError("invalid VisionRig URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise PhysicalPerceptionQualificationError(
            "VisionRig URL must be a loopback HTTP(S) origin"
        )
    if host.lower() != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise PhysicalPerceptionQualificationError(
                    "VisionRig URL must be loopback"
                )
        except ValueError as exc:
            raise PhysicalPerceptionQualificationError(
                "VisionRig URL must use localhost or a loopback IP"
            ) from exc
    return raw


def _http_json(url: str, *, timeout: float) -> Mapping[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise PhysicalPerceptionQualificationError(
                    f"VisionRig returned HTTP {response.status}"
                )
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise PhysicalPerceptionQualificationError(
            f"VisionRig returned HTTP {exc.code}"
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PhysicalPerceptionQualificationError(
            "VisionRig loopback request failed"
        ) from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise PhysicalPerceptionQualificationError(
            "VisionRig response exceeded qualification bound"
        )
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhysicalPerceptionQualificationError(
            "VisionRig response was not valid UTF-8 JSON"
        ) from exc
    if not isinstance(value, Mapping):
        raise PhysicalPerceptionQualificationError(
            "VisionRig response must be a JSON object"
        )
    return value


def _bridge_endpoint_is_loopback(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not host:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _physical_sources(
    status: Mapping[str, Any],
    *,
    requested_source_id: str | None,
) -> list[Mapping[str, Any]]:
    sources = status.get("sources")
    if not isinstance(sources, list):
        raise PhysicalPerceptionQualificationError(
            "sensor status lacks source inventory"
        )
    result: list[Mapping[str, Any]] = []
    for raw in sources:
        if not isinstance(raw, Mapping):
            continue
        source_id = raw.get("source_id")
        if requested_source_id is not None and source_id != requested_source_id:
            continue
        if raw.get("source_type") not in PHYSICAL_SOURCE_TYPES:
            continue
        if raw.get("presence") != "online":
            continue
        accepted = raw.get("accepted_frames")
        sequence = raw.get("last_sequence")
        if not isinstance(accepted, int) or accepted < 1:
            continue
        if not isinstance(sequence, int) or sequence < 0:
            continue
        result.append(raw)
    result.sort(
        key=lambda item: (
            float(item.get("age_seconds", 1e9)),
            str(item.get("source_id", "")),
        )
    )
    return result


def _event_batch(
    base: str,
    *,
    after_cursor: int,
    http_json: Callable[..., Mapping[str, Any]],
    request_timeout: float,
) -> Mapping[str, Any]:
    query = urllib.parse.urlencode({"after_cursor": after_cursor, "limit": 256})
    batch = http_json(
        base + "/api/v1/perception/events?" + query,
        timeout=request_timeout,
    )
    if batch.get("schema_id") != "visionrig/event-batch/v1":
        raise PhysicalPerceptionQualificationError("event journal schema mismatch")
    if batch.get("gap") is True:
        raise PhysicalPerceptionQualificationError(
            "event journal gap detected during qualification"
        )
    entries = batch.get("entries")
    if not isinstance(entries, list):
        raise PhysicalPerceptionQualificationError("event journal entries missing")
    return batch


def _validate_bridge_binding(
    health: Mapping[str, Any],
    *,
    event: Mapping[str, Any],
    source_id: str,
    frame_sequence: int,
) -> dict[str, Any] | None:
    bridge = health.get("modelrig_bridge")
    if not isinstance(bridge, Mapping) or bridge.get("enabled") is not True:
        raise PhysicalPerceptionQualificationError(
            "VisionRig ModelRig bridge is not enabled"
        )
    if not _bridge_endpoint_is_loopback(bridge.get("endpoint")):
        raise PhysicalPerceptionQualificationError(
            "VisionRig ModelRig bridge is not loopback-bound"
        )
    successful_results = bridge.get("successful_results")
    candidates: list[Mapping[str, Any]] = []
    if isinstance(successful_results, list):
        candidates.extend(
            item for item in successful_results if isinstance(item, Mapping)
        )
    last_result = bridge.get("last_result")
    if isinstance(last_result, Mapping):
        candidates.append(last_result)

    result = next(
        (
            item
            for item in reversed(candidates)
            if item.get("source_id") == source_id
            and item.get("frame_sequence") == frame_sequence
            and item.get("status") in {"published", "replayed"}
        ),
        None,
    )
    if result is None:
        return None

    receipt = result.get("receipt")
    if not isinstance(receipt, Mapping):
        raise PhysicalPerceptionQualificationError(
            "matching bridge result lacks ModelRig receipt"
        )
    expected_ref = _event_ref(event)
    if receipt.get("schema") != "kaliv-consciousness-core/visionrig-admission/v1":
        raise PhysicalPerceptionQualificationError(
            "ModelRig VisionRig receipt schema mismatch"
        )
    if receipt.get("visionrig_event_ref") != expected_ref:
        raise PhysicalPerceptionQualificationError(
            "ModelRig receipt is not bound to exact VisionRig event"
        )
    if not _SHA_REF.fullmatch(str(receipt.get("visionrig_event_ref", ""))):
        raise PhysicalPerceptionQualificationError(
            "ModelRig receipt event reference is malformed"
        )
    if receipt.get("observed_sequence") != frame_sequence:
        raise PhysicalPerceptionQualificationError(
            "ModelRig receipt frame sequence mismatch"
        )
    if receipt.get("epistemic_status") != "inferred":
        raise PhysicalPerceptionQualificationError(
            "ModelRig receipt epistemic status is not inferred"
        )
    if receipt.get("model_calls") != 0:
        raise PhysicalPerceptionQualificationError(
            "VisionRig admission unexpectedly invoked a model"
        )
    if receipt.get("self_state_store_write_applied") is not False:
        raise PhysicalPerceptionQualificationError(
            "VisionRig admission unexpectedly wrote SelfState"
        )
    for field in (
        "durable_memory_write_authority",
        "execution_authority",
        "scheduling_authority",
        "production_activation",
    ):
        if receipt.get(field) is not False:
            raise PhysicalPerceptionQualificationError(
                f"ModelRig VisionRig receipt overclaimed {field}"
            )
    evidence_ref = receipt.get("evidence_ref")
    if not isinstance(evidence_ref, str) or not evidence_ref.strip():
        raise PhysicalPerceptionQualificationError(
            "ModelRig VisionRig receipt lacks evidence reference"
        )
    return {
        "status": result["status"],
        "visionrig_event_ref": expected_ref,
        "evidence_ref": evidence_ref,
        "cognition_event_id": receipt.get("cognition_event_id"),
        "world_changed": receipt.get("world_changed"),
        "replayed": receipt.get("replayed"),
        "cognition_event_queued": receipt.get("cognition_event_queued"),
        "observed_sequence": frame_sequence,
        "model_calls": 0,
    }


def _success_report(
    *,
    base: str,
    initial_health: Mapping[str, Any],
    selected_id: str,
    selected_type: str,
    baseline_sequence: int,
    baseline_accepted: int,
    candidate_cursor: int | None,
    frame_sequence: int,
    bridge_binding: Mapping[str, Any],
    started: float,
    monotonic: Callable[[], float],
    http_json: Callable[..., Mapping[str, Any]],
    request_timeout: float,
) -> dict[str, Any] | None:
    current_status = http_json(
        base + "/api/v1/sensors/status",
        timeout=request_timeout,
    )
    current_sources = _physical_sources(
        current_status,
        requested_source_id=selected_id,
    )
    if not current_sources:
        raise PhysicalPerceptionQualificationError(
            "physical source stopped being online during qualification"
        )
    final_source = current_sources[0]
    if int(final_source["accepted_frames"]) <= baseline_accepted:
        return None
    elapsed_ms = round((monotonic() - started) * 1000.0, 3)
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "visionrig": {
            "origin": base,
            "health_schema": initial_health.get("schema"),
            "perception_schema": initial_health.get("perception_schema"),
        },
        "physical_source": {
            "source_id": selected_id,
            "source_type": selected_type,
            "device": final_source.get("device"),
            "presence": final_source.get("presence"),
            "baseline_sequence": baseline_sequence,
            "accepted_frames_before": baseline_accepted,
            "accepted_frames_after": final_source.get("accepted_frames"),
        },
        "event": {
            "journal_cursor": candidate_cursor,
            "frame_sequence": frame_sequence,
            "visionrig_event_ref": bridge_binding["visionrig_event_ref"],
        },
        "modelrig_admission": dict(bridge_binding),
        "timing": {
            "qualification_elapsed_ms": elapsed_ms,
            "poll_interval_ms": round(POLL_SECONDS * 1000.0, 3),
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


def qualify_physical_perception(
    visionrig_url: str,
    *,
    source_id: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    request_timeout: float = 5.0,
    http_json: Callable[..., Mapping[str, Any]] = _http_json,
    monotonic: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if timeout_seconds <= 0 or timeout_seconds > 600:
        raise PhysicalPerceptionQualificationError(
            "qualification timeout must be in (0, 600] seconds"
        )
    base = _loopback_origin(visionrig_url)

    health = http_json(base + "/health", timeout=request_timeout)
    if health.get("status") != "ok" or health.get("service") != "visionrig":
        raise PhysicalPerceptionQualificationError("VisionRig health is not ok")
    if health.get("perception_schema") != "visionrig/perception-event/v3":
        raise PhysicalPerceptionQualificationError(
            "VisionRig perception schema is not v3"
        )
    bridge = health.get("modelrig_bridge")
    if not isinstance(bridge, Mapping) or bridge.get("enabled") is not True:
        raise PhysicalPerceptionQualificationError(
            "VisionRig ModelRig bridge is not enabled"
        )
    if not _bridge_endpoint_is_loopback(bridge.get("endpoint")):
        raise PhysicalPerceptionQualificationError(
            "VisionRig ModelRig bridge is not loopback-bound"
        )

    status = http_json(base + "/api/v1/sensors/status", timeout=request_timeout)
    sources = _physical_sources(status, requested_source_id=source_id)
    if not sources:
        suffix = (
            f" for source {source_id!r}" if source_id is not None else ""
        )
        raise PhysicalPerceptionQualificationError(
            "no online physical camera/VR source with accepted frames" + suffix
        )
    selected = sources[0]
    selected_id = str(selected["source_id"])
    selected_type = str(selected["source_type"])
    baseline_sequence = int(selected["last_sequence"])
    baseline_accepted = int(selected["accepted_frames"])

    initial_batch = _event_batch(
        base,
        after_cursor=0,
        http_json=http_json,
        request_timeout=request_timeout,
    )
    newest = initial_batch.get("newest_available_cursor")
    baseline_cursor = int(newest) if isinstance(newest, int) else 0

    started = monotonic()
    cursor = baseline_cursor
    candidate_event: Mapping[str, Any] | None = None
    candidate_cursor: int | None = None
    bridge_binding: dict[str, Any] | None = None

    while monotonic() - started <= timeout_seconds:
        batch = _event_batch(
            base,
            after_cursor=cursor,
            http_json=http_json,
            request_timeout=request_timeout,
        )
        entries = batch.get("entries", [])
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            entry_cursor = entry.get("cursor")
            event = entry.get("event")
            if not isinstance(entry_cursor, int) or not isinstance(event, Mapping):
                continue
            cursor = max(cursor, entry_cursor)
            source = event.get("source")
            if not isinstance(source, Mapping):
                continue
            frame_sequence = event.get("frame_sequence")
            if (
                source.get("source_id") != selected_id
                or source.get("source_type") != selected_type
                or not isinstance(frame_sequence, int)
                or frame_sequence <= baseline_sequence
                or event.get("schema_id") != "visionrig/perception-event/v3"
                or event.get("production_authority") is not False
            ):
                continue
            candidate_event = event
            candidate_cursor = entry_cursor

            current_health = http_json(base + "/health", timeout=request_timeout)
            bridge_binding = _validate_bridge_binding(
                current_health,
                event=event,
                source_id=selected_id,
                frame_sequence=frame_sequence,
            )
            if bridge_binding is not None:
                report = _success_report(
                    base=base,
                    initial_health=health,
                    selected_id=selected_id,
                    selected_type=selected_type,
                    baseline_sequence=baseline_sequence,
                    baseline_accepted=baseline_accepted,
                    candidate_cursor=candidate_cursor,
                    frame_sequence=frame_sequence,
                    bridge_binding=bridge_binding,
                    started=started,
                    monotonic=monotonic,
                    http_json=http_json,
                    request_timeout=request_timeout,
                )
                if report is not None:
                    return report

        next_cursor = batch.get("next_cursor")
        if isinstance(next_cursor, int):
            cursor = max(cursor, next_cursor)

        if candidate_event is not None:
            current_health = http_json(base + "/health", timeout=request_timeout)
            frame_sequence = candidate_event.get("frame_sequence")
            if isinstance(frame_sequence, int):
                bridge_binding = _validate_bridge_binding(
                    current_health,
                    event=candidate_event,
                    source_id=selected_id,
                    frame_sequence=frame_sequence,
                )
                if bridge_binding is not None:
                    report = _success_report(
                        base=base,
                        initial_health=health,
                        selected_id=selected_id,
                        selected_type=selected_type,
                        baseline_sequence=baseline_sequence,
                        baseline_accepted=baseline_accepted,
                        candidate_cursor=candidate_cursor,
                        frame_sequence=frame_sequence,
                        bridge_binding=bridge_binding,
                        started=started,
                        monotonic=monotonic,
                        http_json=http_json,
                        request_timeout=request_timeout,
                    )
                    if report is not None:
                        return report

        sleep_fn(POLL_SECONDS)

    if candidate_event is None:
        raise PhysicalPerceptionQualificationError(
            "no fresh physical PerceptionEvent arrived before timeout"
        )
    raise PhysicalPerceptionQualificationError(
        "fresh physical perception never produced a matching verified ModelRig bridge receipt"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--visionrig-url",
        default=os.environ.get(
            "VISIONRIG_QUALIFICATION_URL",
            DEFAULT_VISIONRIG_URL,
        ),
    )
    parser.add_argument("--source-id")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    try:
        report = qualify_physical_perception(
            args.visionrig_url,
            source_id=args.source_id,
            timeout_seconds=args.timeout,
        )
    except PhysicalPerceptionQualificationError as exc:
        failure = {
            "schema": SCHEMA,
            "generated_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "error": {
                "type": type(exc).__name__,
                "message": str(exc)[:500],
            },
            "gate": {
                "passed": False,
                "physical_perception_qualified": False,
                "raw_sensor_authority_granted": False,
                "identity_authority_granted": False,
                "production_activation": False,
            },
        }
        _write_json_atomic(args.report, failure)
        print(json.dumps(failure, indent=2, sort_keys=True))
        return 1

    _write_json_atomic(args.report, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
