from fastapi.testclient import TestClient

from visionrig.api import create_app


def test_health_capabilities_ingest_and_event_journal() -> None:
    client = TestClient(create_app())
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["capture_queue"]["capacity"] == 4
    assert health.json()["perception_schema"] == "visionrig/perception-event/v3"

    capabilities = client.get("/api/v1/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["schema"] == "visionrig/capabilities/v2"

    response = client.post(
        "/api/v1/perception/ingest",
        json={
            "source": {"source_id": "dev", "source_type": "synthetic"},
            "frame_sequence": 1,
            "payload": {
                "entities": [{
                    "entity_id": "person-1",
                    "kind": "person",
                    "label": "person",
                    "confidence": 0.99
                }],
                "landmarks": [{
                    "observation_id": "pose-1",
                    "group": "pose",
                    "landmarks": [{
                        "name": "nose",
                        "x": 0.5,
                        "y": 0.4,
                        "z": 0.0,
                        "confidence": 0.9
                    }]
                }]
            }
        },
    )
    assert response.status_code == 200
    assert response.json()["schema_id"] == "visionrig/perception-event/v3"

    batch = client.get("/api/v1/perception/events?after_cursor=0")
    assert batch.status_code == 200
    body = batch.json()
    assert body["schema_id"] == "visionrig/event-batch/v1"
    assert len(body["entries"]) == 1
    assert body["entries"][0]["event"]["event_id"] == response.json()["event_id"]
