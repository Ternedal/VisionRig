from fastapi.testclient import TestClient

from visionrig.api import create_app


def test_health_and_ingest() -> None:
    client = TestClient(create_app())
    assert client.get("/health").status_code == 200

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
