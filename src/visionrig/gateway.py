"""Authenticated cross-device gateway for VisionRig sensor frames.

The gateway exposes only frame ingress and forwards only to a loopback VisionRig
core service. It does not proxy journal, world-state, model or profile APIs.
"""
from __future__ import annotations

from dataclasses import dataclass
import hmac
import ipaddress
import os
import secrets
from typing import Literal
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response

from .http_io import read_bounded_body


class GatewayConfigError(RuntimeError):
    pass


def _validate_loopback_target(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise GatewayConfigError("gateway target must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise GatewayConfigError("gateway target must not contain credentials")
    if parsed.query or parsed.fragment:
        raise GatewayConfigError("gateway target may not contain query or fragment")
    host = parsed.hostname
    if host is None:
        raise GatewayConfigError("gateway target has no hostname")
    loopback = host.lower() == "localhost"
    if not loopback:
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
    if not loopback:
        raise GatewayConfigError("gateway target must be loopback")
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    token: str
    bind_host: str = "127.0.0.1"
    bind_port: int = 8111
    target_base_url: str = "http://127.0.0.1:8110"
    max_payload_bytes: int = 8 * 1024 * 1024
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if len(self.token) < 32:
            raise GatewayConfigError("gateway token must be at least 32 characters")
        if self.bind_host.strip() in {"", "0.0.0.0", "::", "[::]"}:
            raise GatewayConfigError(
                "gateway must bind an explicit interface address, not a wildcard"
            )
        if not 1 <= self.bind_port <= 65535:
            raise GatewayConfigError("gateway port must be between 1 and 65535")
        if not 1024 <= self.max_payload_bytes <= 64 * 1024 * 1024:
            raise GatewayConfigError(
                "gateway max payload must be between 1024 and 67108864 bytes"
            )
        if not 0.1 <= self.timeout_seconds <= 120.0:
            raise GatewayConfigError("gateway timeout must be between 0.1 and 120 seconds")
        object.__setattr__(
            self,
            "target_base_url",
            _validate_loopback_target(self.target_base_url),
        )

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        token = os.getenv("VISIONRIG_GATEWAY_TOKEN", "")
        host = os.getenv("VISIONRIG_GATEWAY_BIND_HOST", "127.0.0.1")
        target = os.getenv("VISIONRIG_GATEWAY_TARGET", "http://127.0.0.1:8110")
        try:
            port = int(os.getenv("VISIONRIG_GATEWAY_PORT", "8111"))
            max_payload = int(
                os.getenv("VISIONRIG_GATEWAY_MAX_SENSOR_FRAME_BYTES", str(8 * 1024 * 1024))
            )
            timeout = float(os.getenv("VISIONRIG_GATEWAY_TIMEOUT_SECONDS", "30"))
        except ValueError as exc:
            raise GatewayConfigError("invalid numeric gateway environment value") from exc
        return cls(
            token=token,
            bind_host=host,
            bind_port=port,
            target_base_url=target,
            max_payload_bytes=max_payload,
            timeout_seconds=timeout,
        )


def generate_gateway_token() -> str:
    return secrets.token_urlsafe(32)


def _has_valid_bearer(request: Request, token: str) -> bool:
    authorization = request.headers.get("authorization", "")
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return False
    supplied = authorization[len(prefix):]
    if not supplied:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), token.encode("utf-8"))


def create_gateway_app(
    config: GatewayConfig,
    *,
    client: httpx.AsyncClient | None = None,
) -> FastAPI:
    app = FastAPI(title="VisionRig Sensor Gateway", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "visionrig-sensor-gateway",
            "schema": "visionrig/sensor-gateway-health/v1",
            "target_scope": "loopback-only",
            "routes": ["/api/v1/frames/ingest"],
        }

    @app.post("/api/v1/frames/ingest")
    async def ingest(
        request: Request,
        source_id: str = Query(min_length=1, max_length=128),
        source_type: Literal["camera", "screen", "vr", "image"] = Query(),
        frame_sequence: int = Query(ge=0),
        device: str | None = Query(default=None, max_length=256),
        dropped_frames: int = Query(default=0, ge=0),
    ) -> Response:
        if not _has_valid_bearer(request, config.token):
            raise HTTPException(
                status_code=401,
                detail="invalid gateway bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        content_type = request.headers.get("content-type", "")
        payload = await read_bounded_body(request, config.max_payload_bytes)
        params: dict[str, str | int] = {
            "source_id": source_id,
            "source_type": source_type,
            "frame_sequence": frame_sequence,
            "dropped_frames": dropped_frames,
        }
        if device is not None:
            params["device"] = device

        target = config.target_base_url + "/api/v1/frames/ingest"
        try:
            if client is None:
                async with httpx.AsyncClient(timeout=config.timeout_seconds) as local_client:
                    upstream = await local_client.post(
                        target,
                        params=params,
                        content=payload,
                        headers={"content-type": content_type},
                    )
            else:
                upstream = await client.post(
                    target,
                    params=params,
                    content=payload,
                    headers={"content-type": content_type},
                    timeout=config.timeout_seconds,
                )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail="VisionRig loopback ingress unavailable",
            ) from exc

        media_type = upstream.headers.get("content-type", "application/json").split(";", 1)[0]
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=media_type,
            headers={"X-VisionRig-Gateway": "1"},
        )

    return app
