from pathlib import Path

import httpx

from visionrig.producer import GatewayFrameProducer
from visionrig.producer_state import ProducerStateStore


TOKEN = "z" * 48


def test_restart_recovers_crashed_inflight_frame_into_next_receipt(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "producer-state.json"
    store = ProducerStateStore(state_path)

    producer = GatewayFrameProducer(
        gateway_url="http://100.64.0.2:8111",
        token=TOKEN,
        source_id="quest",
        source_type="vr",
        state_store=store,
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
    )

    # Reserve directly to simulate process death after capture reservation and
    # before an HTTP outcome can be persisted.
    sequence, pending = store.reserve(producer._state_key)
    assert sequence == 0
    assert pending == 0
    producer._client.close()

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["sequence"] = int(request.url.params["frame_sequence"])
        seen["dropped"] = int(request.url.params["dropped_frames"])
        return httpx.Response(
            200,
            json={
                "schema_id": "visionrig/sensor-frame-receipt/v1",
                "status": "processed",
                "source_id": "quest",
                "source_type": "vr",
                "frame_sequence": seen["sequence"],
                "event_id": "evt-recovered",
                "dropped_frames": seen["dropped"],
                "production_authority": False,
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        restarted = GatewayFrameProducer(
            gateway_url="http://100.64.0.2:8111",
            token=TOKEN,
            source_id="quest",
            source_type="vr",
            state_store=ProducerStateStore(state_path),
            client=client,
        )
        result = restarted.send_encoded(b"fresh-frame")

    assert result.status == "accepted"
    assert seen == {"sequence": 1, "dropped": 1}
    assert restarted.stats().pending_dropped_frames == 0
    assert restarted.stats().next_sequence == 2
