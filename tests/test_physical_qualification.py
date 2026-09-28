from __future__ import annotations

import hashlib
import json

import pytest

from visionrig.physical_qualification import (
    PhysicalPerceptionQualificationError,
    _physical_sources,
    _validate_bridge_binding,
    qualify_physical_perception,
)


def _event(*, sequence: int = 11) -> dict:
    return {
        "schema_id": "visionrig/perception-event/v4",
        "event_id": "evt-physical-11",
        "observed_at": "2026-09-28T05:00:00Z",
        "source": {
            "source_id": "kinect-v2-0",
            "source_type": "camera",
            "device": "kinect-v2",
        },
        "frame_sequence": sequence,
        "entities": [
            {
                "entity_id": "person-1",
                "kind": "person",
                "label": "person",
                "confidence": 0.95,
                "bbox": None,
                "track_id": None,
                "identity_hint": None,
            }
        ],
        "relations": [],
        "landmarks": [],
        "depth": [],
        "scene_label": None,
        "scene_confidence": None,
        "dropped_frames": 0,
        "production_authority": False,
    }


def _event_ref(event: dict) -> str:
    payload = json.dumps(
        event,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "visionrig-event:" + hashlib.sha256(payload).hexdigest()


def _source(*, accepted: int, sequence: int) -> dict:
    return {
        "source_id": "kinect-v2-0",
        "source_type": "camera",
        "heartbeat_schema_id": "visionrig/sensor-heartbeat/v6",
        "device": "kinect-v2",
        "capabilities": ["rgb", "depth"],
        "capture_active": True,
        "applied_revision": 1,
        "negotiated_max_payload_bytes": None,
        "negotiated_packet_compression": None,
        "negotiated_packet_target_utilization": None,
        "observed_packet_utilization": None,
        "packet_target_above_streak": 0,
        "packet_target_above_since_utc": None,
        "packet_target_above_seconds": None,
        "packet_target_last_above_utc": None,
        "packet_target_sustained_episode_count": 0,
        "packet_target_last_recovered_utc": None,
        "packet_target_recurrence_count": 0,
        "packet_target_last_recurrence_seconds": None,
        "capability_refreshed_utc": None,
        "capability_refresh_observed_utc": None,
        "capability_refresh_age_seconds": None,
        "capability_refresh_status": "unknown",
        "presence": "online",
        "age_seconds": 0.05,
        "last_sequence": sequence,
        "accepted_frames": accepted,
        "heartbeat_count": 5,
        "dropped_frames_total": 0,
        "packet_transport": None,
        "last_seen_utc": "2026-09-28T05:00:00Z",
    }


def _receipt(event: dict, *, activation: bool = False, world_changed: bool = True) -> dict:
    return {
        "schema": "kaliv-consciousness-core/visionrig-admission/v1",
        "visionrig_event_ref": _event_ref(event),
        "evidence_ref": "world-evidence:" + "a" * 64,
        "cognition_event_id": "cevt-" + "b" * 32,
        "world_changed": world_changed,
        "replayed": False,
        "cognition_event_queued": True,
        "epistemic_status": "inferred",
        "confidence": 0.9,
        "attention_salience": 0.7,
        "observed_sequence": event["frame_sequence"],
        "model_calls": 0,
        "self_state_store_write_applied": False,
        "durable_memory_write_authority": False,
        "execution_authority": False,
        "scheduling_authority": False,
        "production_activation": activation,
    }


def _health(*, event: dict | None = None, activation: bool = False) -> dict:
    result = None
    if event is not None:
        result = {
            "status": "published",
            "source_id": event["source"]["source_id"],
            "frame_sequence": event["frame_sequence"],
            "reason": None,
            "receipt": _receipt(event, activation=activation),
        }
    return {
        "status": "ok",
        "service": "visionrig",
        "schema": "visionrig/health/v59",
        "perception_schema": "visionrig/perception-event/v4",
        "modelrig_bridge": {
            "enabled": True,
            "endpoint": "http://127.0.0.1:8099/experimental/consciousness/visionrig-event",
            "stats": {
                "published": 1 if event is not None else 0,
                "replayed": 0,
                "suppressed": 0,
                "unavailable": 0,
                "rejected": 0,
            },
            "last_status": "published" if event is not None else None,
            "last_result": result,
        },
    }


def test_physical_qualification_binds_fresh_camera_event_to_modelrig_receipt() -> None:
    event = _event()
    health_calls = 0
    status_calls = 0

    def http_json(url: str, *, timeout: float):
        nonlocal health_calls, status_calls
        assert timeout == 5.0
        if url.endswith("/health"):
            health_calls += 1
            return _health(event=event if health_calls > 1 else None)
        if url.endswith("/api/v1/sensors/status"):
            status_calls += 1
            return {
                "schema": "visionrig/sensor-ingress/v9",
                "sources": [
                    _source(
                        accepted=11 if status_calls > 1 else 10,
                        sequence=11 if status_calls > 1 else 10,
                    )
                ],
            }
        if "after_cursor=0" in url:
            return {
                "schema_id": "visionrig/event-batch/v1",
                "entries": [],
                "next_cursor": 0,
                "oldest_available_cursor": 1,
                "newest_available_cursor": 4,
                "gap": False,
            }
        if "after_cursor=4" in url:
            return {
                "schema_id": "visionrig/event-batch/v1",
                "entries": [{"cursor": 5, "event": event}],
                "next_cursor": 5,
                "oldest_available_cursor": 1,
                "newest_available_cursor": 5,
                "gap": False,
            }
        raise AssertionError(url)

    tick = [0.0]

    def monotonic() -> float:
        tick[0] += 0.01
        return tick[0]

    report = qualify_physical_perception(
        "http://127.0.0.1:8110",
        source_id="kinect-v2-0",
        timeout_seconds=2.0,
        http_json=http_json,
        monotonic=monotonic,
        sleep_fn=lambda _seconds: None,
    )

    assert report["gate"]["passed"] is True
    assert report["gate"]["physical_perception_qualified"] is True
    assert report["gate"]["production_activation"] is False
    assert report["physical_source"]["source_type"] == "camera"
    assert report["event"]["frame_sequence"] == 11
    assert report["event"]["visionrig_event_ref"] == _event_ref(event)
    assert report["event"]["semantic_observation_count"] == 1
    assert report["event"]["semantic_observation_kinds"] == ("entities",)
    assert report["event"]["semantic_observation_counts"] == {
        "entities": 1,
        "relations": 0,
        "landmarks": 0,
        "depth": 0,
        "infrared": 0,
        "scene": 0,
    }
    assert report["modelrig_admission"]["model_calls"] == 0
    assert report["privacy"] == {
        "raw_frame_included": False,
        "ocr_text_included": False,
        "landmarks_included": False,
        "semantic_payload_included": False,
    }
    encoded = json.dumps(report)
    assert "person-1" not in encoded
    assert '"label"' not in encoded



def test_physical_qualification_ignores_empty_v4_events_until_semantics_arrive() -> None:
    empty_event = _event(sequence=11)
    empty_event["entities"] = []
    semantic_event = _event(sequence=12)
    health_calls = 0
    status_calls = 0
    event_calls = 0

    def http_json(url: str, *, timeout: float):
        nonlocal health_calls, status_calls, event_calls
        if url.endswith("/health"):
            health_calls += 1
            return _health(event=semantic_event if health_calls > 1 else None)
        if url.endswith("/api/v1/sensors/status"):
            status_calls += 1
            return {
                "sources": [
                    _source(
                        accepted=12 if status_calls > 1 else 10,
                        sequence=12 if status_calls > 1 else 10,
                    )
                ]
            }
        if "after_cursor=0" in url:
            return {
                "schema_id": "visionrig/event-batch/v1",
                "entries": [],
                "next_cursor": 0,
                "oldest_available_cursor": 1,
                "newest_available_cursor": 4,
                "gap": False,
            }
        event_calls += 1
        entries = (
            [{"cursor": 5, "event": empty_event}, {"cursor": 6, "event": semantic_event}]
            if event_calls == 1
            else []
        )
        return {
            "schema_id": "visionrig/event-batch/v1",
            "entries": entries,
            "next_cursor": 6,
            "oldest_available_cursor": 1,
            "newest_available_cursor": 6,
            "gap": False,
        }

    tick = [0.0]

    def monotonic() -> float:
        tick[0] += 0.01
        return tick[0]

    report = qualify_physical_perception(
        "http://127.0.0.1:8110",
        source_id="kinect-v2-0",
        timeout_seconds=2.0,
        http_json=http_json,
        monotonic=monotonic,
        sleep_fn=lambda _seconds: None,
    )

    assert report["event"]["frame_sequence"] == 12
    assert report["event"]["visionrig_event_ref"] == _event_ref(semantic_event)

def test_physical_qualification_rejects_modelrig_authority_overclaim() -> None:
    event = _event()

    def http_json(url: str, *, timeout: float):
        if url.endswith("/health"):
            return _health(event=event, activation=True)
        if url.endswith("/api/v1/sensors/status"):
            return {"sources": [_source(accepted=10, sequence=10)]}
        if "after_cursor=0" in url:
            return {
                "schema_id": "visionrig/event-batch/v1",
                "entries": [],
                "next_cursor": 0,
                "oldest_available_cursor": 1,
                "newest_available_cursor": 4,
                "gap": False,
            }
        return {
            "schema_id": "visionrig/event-batch/v1",
            "entries": [{"cursor": 5, "event": event}],
            "next_cursor": 5,
            "oldest_available_cursor": 1,
            "newest_available_cursor": 5,
            "gap": False,
        }

    with pytest.raises(
        PhysicalPerceptionQualificationError,
        match="overclaimed production_activation",
    ):
        qualify_physical_perception(
            "http://127.0.0.1:8110",
            source_id="kinect-v2-0",
            timeout_seconds=1.0,
            http_json=http_json,
            monotonic=lambda: 0.1,
            sleep_fn=lambda _seconds: None,
        )


def test_physical_qualification_rejects_nonphysical_or_remote_inputs() -> None:
    with pytest.raises(
        PhysicalPerceptionQualificationError,
        match="must be loopback",
    ):
        qualify_physical_perception("http://192.168.1.10:8110")

    def http_json(url: str, *, timeout: float):
        if url.endswith("/health"):
            return _health()
        if url.endswith("/api/v1/sensors/status"):
            source = _source(accepted=10, sequence=10)
            source["source_type"] = "image"
            return {"sources": [source]}
        raise AssertionError(url)

    with pytest.raises(
        PhysicalPerceptionQualificationError,
        match="no online physical camera/VR source",
    ):
        qualify_physical_perception(
            "http://127.0.0.1:8110",
            http_json=http_json,
        )


def test_physical_qualification_retries_until_ingress_counter_advances() -> None:
    event = _event()
    health_calls = 0
    status_calls = 0
    event_calls = 0

    def http_json(url: str, *, timeout: float):
        nonlocal health_calls, status_calls, event_calls
        if url.endswith("/health"):
            health_calls += 1
            return _health(event=event if health_calls > 1 else None)
        if url.endswith("/api/v1/sensors/status"):
            status_calls += 1
            accepted = 10 if status_calls < 3 else 11
            sequence = 10 if status_calls == 1 else 11
            return {"sources": [_source(accepted=accepted, sequence=sequence)]}
        if "after_cursor=0" in url:
            return {
                "schema_id": "visionrig/event-batch/v1",
                "entries": [],
                "next_cursor": 0,
                "oldest_available_cursor": 1,
                "newest_available_cursor": 4,
                "gap": False,
            }
        event_calls += 1
        return {
            "schema_id": "visionrig/event-batch/v1",
            "entries": (
                [{"cursor": 5, "event": event}]
                if event_calls == 1
                else []
            ),
            "next_cursor": 5,
            "oldest_available_cursor": 1,
            "newest_available_cursor": 5,
            "gap": False,
        }

    tick = [0.0]

    def monotonic() -> float:
        tick[0] += 0.01
        return tick[0]

    report = qualify_physical_perception(
        "http://127.0.0.1:8110",
        source_id="kinect-v2-0",
        timeout_seconds=2.0,
        http_json=http_json,
        monotonic=monotonic,
        sleep_fn=lambda _seconds: None,
    )

    assert report["gate"]["physical_perception_qualified"] is True
    assert report["physical_source"]["accepted_frames_before"] == 10
    assert report["physical_source"]["accepted_frames_after"] == 11
    assert status_calls >= 3


def test_physical_qualification_accepts_bridge_receipt_one_poll_later() -> None:
    event = _event()
    health_calls = 0
    event_calls = 0
    status_calls = 0

    def http_json(url: str, *, timeout: float):
        nonlocal health_calls, event_calls, status_calls
        if url.endswith("/health"):
            health_calls += 1
            # Initial health + first post-event health have no exact result.
            # The following poll exposes the verified synchronous bridge result.
            return _health(event=event if health_calls >= 3 else None)
        if url.endswith("/api/v1/sensors/status"):
            status_calls += 1
            return {
                "sources": [
                    _source(
                        accepted=11 if status_calls > 1 else 10,
                        sequence=11 if status_calls > 1 else 10,
                    )
                ]
            }
        if "after_cursor=0" in url:
            return {
                "schema_id": "visionrig/event-batch/v1",
                "entries": [],
                "next_cursor": 0,
                "oldest_available_cursor": 1,
                "newest_available_cursor": 4,
                "gap": False,
            }
        event_calls += 1
        return {
            "schema_id": "visionrig/event-batch/v1",
            "entries": (
                [{"cursor": 5, "event": event}]
                if event_calls == 1
                else []
            ),
            "next_cursor": 5,
            "oldest_available_cursor": 1,
            "newest_available_cursor": 5,
            "gap": False,
        }

    tick = [0.0]

    def monotonic() -> float:
        tick[0] += 0.01
        return tick[0]

    report = qualify_physical_perception(
        "http://127.0.0.1:8110",
        source_id="kinect-v2-0",
        timeout_seconds=2.0,
        http_json=http_json,
        monotonic=monotonic,
        sleep_fn=lambda _seconds: None,
    )
    assert report["gate"]["physical_perception_qualified"] is True
    assert health_calls >= 3


def test_physical_source_selection_excludes_inactive_online_source() -> None:
    inactive = _source(accepted=20, sequence=20)
    inactive["capture_active"] = False
    inactive["age_seconds"] = 0.01

    active = _source(accepted=10, sequence=10)
    active["source_id"] = "camera-active"
    active["age_seconds"] = 0.5

    selected = _physical_sources(
        {"sources": [inactive, active]},
        requested_source_id=None,
    )

    assert [item["source_id"] for item in selected] == ["camera-active"]


def test_bridge_binding_ignores_stale_same_sequence_receipt() -> None:
    current = _event(sequence=11)
    stale = _event(sequence=11)
    stale["event_id"] = "evt-previous-registration"

    stale_result = {
        "status": "published",
        "source_id": "kinect-v2-0",
        "frame_sequence": 11,
        "reason": None,
        "receipt": _receipt(stale),
    }
    health = _health()
    health["modelrig_bridge"]["successful_results"] = [stale_result]
    health["modelrig_bridge"]["last_result"] = None

    assert (
        _validate_bridge_binding(
            health,
            event=current,
            source_id="kinect-v2-0",
            frame_sequence=11,
        )
        is None
    )

    current_result = {
        "status": "published",
        "source_id": "kinect-v2-0",
        "frame_sequence": 11,
        "reason": None,
        "receipt": _receipt(current),
    }
    health["modelrig_bridge"]["successful_results"].append(current_result)

    binding = _validate_bridge_binding(
        health,
        event=current,
        source_id="kinect-v2-0",
        frame_sequence=11,
    )
    assert binding is not None
    assert binding["visionrig_event_ref"] == _event_ref(current)


def test_physical_qualification_semantic_summary_is_privacy_safe() -> None:
    event = _event()
    event["entities"][0]["label"] = "sensitive-label"
    event["scene_label"] = "private-scene"
    event["infrared"] = [{
        "mean_intensity": 0.1,
        "contrast": 0.2,
        "hotspot_fraction": 0.3,
        "sample_count": 42,
        "method": "kinect-v2-infrared-summary",
    }]
    from visionrig.physical_qualification import _semantic_summary
    summary = _semantic_summary(event)
    encoded = json.dumps(summary)
    assert summary["observation_count"] == 3
    assert summary["observation_kinds"] == ("entities", "infrared", "scene")
    assert "sensitive-label" not in encoded
    assert "private-scene" not in encoded
    assert "mean_intensity" not in encoded


def test_physical_qualification_rejects_receipt_without_world_change() -> None:
    event = _event()
    health = _health(event=event)
    health["modelrig_bridge"]["last_result"]["receipt"]["world_changed"] = False
    with pytest.raises(PhysicalPerceptionQualificationError, match="did not prove a WorldState change"):
        _validate_bridge_binding(health, event=event, source_id="kinect-v2-0", frame_sequence=event["frame_sequence"])
