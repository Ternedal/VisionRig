from threading import Lock

import pytest

from visionrig.contracts import SourceDescriptor
from visionrig.pipeline import PerceptionPipeline
from visionrig.runtime import VisionRuntime
from visionrig.sensor_ingress import (
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
