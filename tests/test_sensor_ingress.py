from datetime import datetime, timedelta, timezone
from threading import Lock

import pytest

from visionrig.contracts import SourceDescriptor
from visionrig.pipeline import PerceptionPipeline
from visionrig.runtime import VisionRuntime
from visionrig.sensor_ingress import (
    SensorHeartbeat,
    SensorIngress,
    SensorIngressBusy,
    SensorMediaTypeError,
    SensorPayloadTooLarge,
    SensorSequenceError,
)


class FakeDecoder:
    def decode(self, payload: bytes, content_type: str):
        return {"payload": payload, "content_type": content_type}


def test_sensor_ingress_processes_and_journals_frame() -> None:
    runtime = VisionRuntime(PerceptionPipeline())
    ingress = SensorIngress(runtime, decoder=FakeDecoder(), max_payload_bytes=1024)

    receipt = ingress.process_encoded(
        source_id="kaliv-vr-left",
        source_type="vr",
        frame_sequence=1,
        payload=b"frame",
        content_type="image/jpeg",
        device="quest-camera",
        dropped_frames=2,
    )

    assert receipt.status == "processed"
    assert receipt.source_id == "kaliv-vr-left"
    assert receipt.dropped_frames == 2
    batch = runtime.events(after_cursor=0)
    assert len(batch.entries) == 1
    assert batch.entries[0].event.source.source_type == "vr"


def test_sensor_ingress_rejects_stale_sequence() -> None:
    runtime = VisionRuntime(PerceptionPipeline())
    ingress = SensorIngress(runtime, decoder=FakeDecoder(), max_payload_bytes=1024)
    kwargs = dict(
        source_id="cam",
        source_type="camera",
        payload=b"x",
        content_type="image/jpeg",
    )
    ingress.process_encoded(frame_sequence=4, **kwargs)
    with pytest.raises(SensorSequenceError):
        ingress.process_encoded(frame_sequence=4, **kwargs)


def test_sensor_ingress_rejects_over_size_and_media_type() -> None:
    ingress = SensorIngress(
        VisionRuntime(PerceptionPipeline()),
        decoder=FakeDecoder(),
        max_payload_bytes=1024,
    )
    with pytest.raises(SensorPayloadTooLarge):
        ingress.process_encoded(
            source_id="cam",
            source_type="camera",
            frame_sequence=1,
            payload=b"x" * 1025,
            content_type="image/jpeg",
        )
    with pytest.raises(SensorMediaTypeError):
        ingress.process_encoded(
            source_id="cam",
            source_type="camera",
            frame_sequence=1,
            payload=b"x",
            content_type="application/octet-stream",
        )


def test_sensor_ingress_fails_fast_when_processing_slot_is_busy() -> None:
    ingress = SensorIngress(
        VisionRuntime(PerceptionPipeline()),
        decoder=FakeDecoder(),
        max_payload_bytes=1024,
    )
    assert ingress._processing.acquire(blocking=False)  # contract-level overload test
    try:
        with pytest.raises(SensorIngressBusy):
            ingress.process_encoded(
                source_id="cam",
                source_type="camera",
                frame_sequence=1,
                payload=b"x",
                content_type="image/jpeg",
            )
    finally:
        ingress._processing.release()


def test_negotiation_refresh_status_becomes_stale_after_two_intervals() -> None:
    now = [datetime(2026, 9, 27, 7, 0, tzinfo=timezone.utc)]
    ingress = SensorIngress(
        VisionRuntime(PerceptionPipeline()),
        decoder=FakeDecoder(),
        max_payload_bytes=1024,
        clock=lambda: now[0],
    )
    producer_timestamp = datetime(2020, 1, 1, tzinfo=timezone.utc)
    ingress.heartbeat(
        SensorHeartbeat(
            schema_id="visionrig/sensor-heartbeat/v3",
            source_id="kinect",
            source_type="camera",
            negotiated_max_payload_bytes=1024,
            capability_refreshed_utc=producer_timestamp,
            capability_refresh_seconds=30.0,
        )
    )

    current = ingress.stats().sources[0]
    assert current.capability_refreshed_utc == producer_timestamp.isoformat()
    assert current.capability_refresh_observed_utc == now[0].isoformat()
    assert current.capability_refresh_status == "current"
    assert current.capability_refresh_age_seconds == 0.0

    now[0] += timedelta(seconds=61)
    stale = ingress.stats().sources[0]
    assert stale.capability_refresh_status == "stale"
    assert stale.capability_refresh_age_seconds == 61.0


def test_same_reported_refresh_token_does_not_refresh_server_observed_age() -> None:
    now = [datetime(2026, 9, 27, 7, 0, tzinfo=timezone.utc)]
    ingress = SensorIngress(
        VisionRuntime(PerceptionPipeline()),
        decoder=FakeDecoder(),
        max_payload_bytes=1024,
        clock=lambda: now[0],
    )
    reported = datetime(2099, 1, 1, tzinfo=timezone.utc)
    heartbeat = SensorHeartbeat(
        schema_id="visionrig/sensor-heartbeat/v3",
        source_id="kinect",
        source_type="camera",
        negotiated_max_payload_bytes=1024,
        capability_refreshed_utc=reported,
        capability_refresh_seconds=30.0,
    )
    ingress.heartbeat(heartbeat)

    now[0] += timedelta(seconds=40)
    ingress.heartbeat(heartbeat)
    same = ingress.stats().sources[0]
    assert same.capability_refresh_observed_utc == "2026-09-27T07:00:00+00:00"
    assert same.capability_refresh_age_seconds == 40.0
    assert same.capability_refresh_status == "current"

    now[0] += timedelta(seconds=21)
    stale = ingress.stats().sources[0]
    assert stale.capability_refresh_age_seconds == 61.0
    assert stale.capability_refresh_status == "stale"


def test_new_reported_refresh_token_resets_server_observed_age() -> None:
    now = [datetime(2026, 9, 27, 7, 0, tzinfo=timezone.utc)]
    ingress = SensorIngress(
        VisionRuntime(PerceptionPipeline()),
        decoder=FakeDecoder(),
        max_payload_bytes=1024,
        clock=lambda: now[0],
    )
    first = datetime(2020, 1, 1, tzinfo=timezone.utc)
    ingress.heartbeat(
        SensorHeartbeat(
            schema_id="visionrig/sensor-heartbeat/v3",
            source_id="kinect",
            source_type="camera",
            negotiated_max_payload_bytes=1024,
            capability_refreshed_utc=first,
            capability_refresh_seconds=30.0,
        )
    )
    now[0] += timedelta(seconds=61)
    assert ingress.stats().sources[0].capability_refresh_status == "stale"

    second = datetime(2020, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
    ingress.heartbeat(
        SensorHeartbeat(
            schema_id="visionrig/sensor-heartbeat/v3",
            source_id="kinect",
            source_type="camera",
            negotiated_max_payload_bytes=1024,
            capability_refreshed_utc=second,
            capability_refresh_seconds=30.0,
        )
    )
    refreshed = ingress.stats().sources[0]
    assert refreshed.capability_refreshed_utc == second.isoformat()
    assert refreshed.capability_refresh_observed_utc == now[0].isoformat()
    assert refreshed.capability_refresh_age_seconds == 0.0
    assert refreshed.capability_refresh_status == "current"


def test_heartbeat_v4_tracks_negotiated_compression() -> None:
    ingress = SensorIngress(
        VisionRuntime(PerceptionPipeline()),
        decoder=FakeDecoder(),
        max_payload_bytes=1024,
    )
    ingress.heartbeat(
        SensorHeartbeat(
            schema_id="visionrig/sensor-heartbeat/v4",
            source_id="kinect-v4",
            source_type="camera",
            negotiated_max_payload_bytes=1024,
            negotiated_packet_compression="zlib",
            capability_refreshed_utc=datetime.now(timezone.utc),
            capability_refresh_seconds=30.0,
        )
    )
    source = ingress.stats().sources[0]
    assert source.negotiated_packet_compression == "zlib"


def test_heartbeat_v4_compression_requires_complete_negotiation_tuple() -> None:
    with pytest.raises(Exception, match="complete negotiation telemetry"):
        SensorHeartbeat(
            schema_id="visionrig/sensor-heartbeat/v4",
            source_id="broken-v4",
            source_type="camera",
            negotiated_packet_compression="auto",
        )
