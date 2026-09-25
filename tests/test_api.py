from fastapi.testclient import TestClient

from visionrig.api import create_app


def test_health_capabilities_and_ingest() -> None:
    client = TestClient(create_app())
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["capture_queue"]["capacity"] == 4

    capabilities = client.get("/api/v1/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["schema"] == "visionrig/capabilities/v1"

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
                }]
            }
        },
    )
    assert response.status_code == 200
    assert response.json()["schema_id"] == "visionrig/perception-event/v1"
    assert response.json()["production_authority"] is False
