import httpx
import pytest
from fastapi.testclient import TestClient

from visionrig.gateway import (
    GatewayConfig,
    GatewayConfigError,
    create_gateway_app,
    generate_gateway_token,
)


TOKEN = "t" * 48


def test_gateway_requires_high_entropy_sized_token() -> None:
    with pytest.raises(GatewayConfigError):
        GatewayConfig(token="short")


def test_gateway_rejects_wildcard_bind_and_non_loopback_target() -> None:
    with pytest.raises(GatewayConfigError, match="explicit interface"):
        GatewayConfig(token=TOKEN, bind_host="0.0.0.0")
    with pytest.raises(GatewayConfigError, match="target must be loopback"):
        GatewayConfig(token=TOKEN, target_base_url="http://192.168.1.10:8110")


def test_generated_token_is_long_enough() -> None:
    assert len(generate_gateway_token()) >= 32


def test_gateway_rejects_missing_or_wrong_bearer() -> None:
    app = create_gateway_app(GatewayConfig(token=TOKEN))
    with TestClient(app) as client:
        missing = client.post(
            "/api/v1/frames/ingest",
            params={
                "source_id": "quest",
                "source_type": "vr",
                "frame_sequence": 1,
            },
            content=b"x",
            headers={"content-type": "image/jpeg"},
        )
        wrong = client.post(
            "/api/v1/frames/ingest",
            params={
                "source_id": "quest",
                "source_type": "vr",
                "frame_sequence": 1,
            },
            content=b"x",
            headers={
                "content-type": "image/jpeg",
                "authorization": "Bearer wrong-token-that-is-long-but-still-wrong",
            },
        )
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_gateway_forwards_only_frame_route_and_whitelisted_metadata() -> None:
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["content_type"] = request.headers.get("content-type")
        seen["body"] = await request.aread()
        return httpx.Response(
            200,
            json={
                "schema_id": "visionrig/sensor-frame-receipt/v1",
                "status": "processed",
                "source_id": "quest",
                "source_type": "vr",
                "frame_sequence": 7,
                "event_id": "evt-1",
                "dropped_frames": 0,
                "production_authority": False,
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as upstream:
        app = create_gateway_app(GatewayConfig(token=TOKEN), client=upstream)
        transport_to_app = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport_to_app,
            base_url="http://gateway",
        ) as caller:
            response = await caller.post(
                "/api/v1/frames/ingest",
                params={
                    "source_id": "quest",
                    "source_type": "vr",
                    "frame_sequence": 7,
                    "device": "quest-camera",
                },
                content=b"jpeg",
                headers={
                    "content-type": "image/jpeg",
                    "authorization": f"Bearer {TOKEN}",
                    "x-untrusted": "must-not-forward",
                },
            )

    assert response.status_code == 200
    assert seen["url"].startswith(
        "http://127.0.0.1:8110/api/v1/frames/ingest?"
    )
    assert "source_id=quest" in seen["url"]
    assert "source_type=vr" in seen["url"]
    assert seen["authorization"] is None
    assert seen["content_type"] == "image/jpeg"
    assert seen["body"] == b"jpeg"
    assert response.headers["x-visionrig-gateway"] == "1"


@pytest.mark.asyncio
async def test_gateway_preserves_local_backpressure_status() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "VisionRig sensor ingress is busy"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_gateway_app(GatewayConfig(token=TOKEN), client=upstream)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://gateway",
        ) as caller:
            response = await caller.post(
                "/api/v1/frames/ingest",
                params={
                    "source_id": "screen",
                    "source_type": "screen",
                    "frame_sequence": 2,
                },
                content=b"jpeg",
                headers={
                    "content-type": "image/jpeg",
                    "authorization": f"Bearer {TOKEN}",
                },
            )
    assert response.status_code == 429


def test_gateway_env_requires_token(monkeypatch) -> None:
    monkeypatch.delenv("VISIONRIG_GATEWAY_TOKEN", raising=False)
    with pytest.raises(GatewayConfigError, match="at least 32"):
        GatewayConfig.from_env()
