"""Shared bounded HTTP body utilities."""
from __future__ import annotations

from fastapi import HTTPException, Request


async def read_bounded_body(request: Request, limit: int) -> bytes:
    if limit < 1:
        raise ValueError("limit must be positive")

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid Content-Length") from exc
        if declared < 0:
            raise HTTPException(status_code=400, detail="invalid Content-Length")
        if declared > limit:
            raise HTTPException(status_code=413, detail="request payload too large")

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(status_code=413, detail="request payload too large")
    return bytes(body)
