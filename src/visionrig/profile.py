"""Revisioned .mrvision enrollment and recognition profiles.

The profile stores only derived visual embeddings + bounded metadata. It does not
store raw pixels and it never grants identity authority.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


UnitInterval = Annotated[
    float,
    Field(ge=0.0, le=1.0, strict=True, allow_inf_nan=False),
]
NonEmptyRef = Annotated[str, Field(min_length=1, max_length=256)]
EnrollmentKind = Literal["face", "body", "object", "place"]


class MrVisionError(RuntimeError):
    pass


class MrVisionCryptoUnavailable(MrVisionError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )


class VisualEnrollment(StrictModel):
    enrollment_id: Annotated[str, Field(pattern=r"^venr-[a-f0-9]{32}$")]
    kind: EnrollmentKind
    label: Annotated[str, Field(min_length=1, max_length=256)]
    subject_ref: NonEmptyRef | None = None
    embedding_model_id: Annotated[str, Field(min_length=1, max_length=256)]
    vector: Annotated[tuple[float, ...], Field(min_length=2, max_length=8192)]
    quality: UnitInterval
    source_ref: NonEmptyRef
    enrolled_at: datetime
    revoked: bool = False
    revocation_reason: Annotated[str, Field(min_length=1, max_length=512)] | None = None

    @model_validator(mode="after")
    def normalized_vector_and_revocation(self) -> "VisualEnrollment":
        norm = math.sqrt(sum(value * value for value in self.vector))
        if not math.isfinite(norm) or abs(norm - 1.0) > 1e-4:
            raise ValueError("enrollment vector must be L2-normalized")
        if self.revoked and self.revocation_reason is None:
            raise ValueError("revoked enrollment requires revocation_reason")
        if not self.revoked and self.revocation_reason is not None:
            raise ValueError("active enrollment cannot carry revocation_reason")
        return self


class MrVisionProfile(StrictModel):
    schema_id: Literal["visionrig/mrvision-profile/v1"] = "visionrig/mrvision-profile/v1"
    profile_id: Annotated[str, Field(pattern=r"^mrvision-[a-f0-9]{32}$")]
    revision: Annotated[int, Field(ge=1, strict=True)]
    parent_profile_ref: NonEmptyRef | None = None
    created_at: datetime
    updated_at: datetime
    enrollments: Annotated[tuple[VisualEnrollment, ...], Field(max_length=4096)]
    identity_authority: Literal[False] = False
    raw_pixels_persisted: Literal[False] = False

    @model_validator(mode="after")
    def validate_profile(self) -> "MrVisionProfile":
        ids = [item.enrollment_id for item in self.enrollments]
        if len(ids) != len(set(ids)):
            raise ValueError("enrollment ids must be unique")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        return self


class MrVisionEnvelope(StrictModel):
    schema_id: Literal["visionrig/mrvision-envelope/v1"] = "visionrig/mrvision-envelope/v1"
    algorithm: Literal["AES-256-GCM"] = "AES-256-GCM"
    profile_ref: NonEmptyRef
    nonce_b64: Annotated[str, Field(min_length=16, max_length=64)]
    ciphertext_b64: Annotated[str, Field(min_length=16)]
    identity_authority: Literal[False] = False


class RecognitionMatch(StrictModel):
    enrollment_id: Annotated[str, Field(pattern=r"^venr-[a-f0-9]{32}$")]
    kind: EnrollmentKind
    label: Annotated[str, Field(min_length=1, max_length=256)]
    subject_ref: NonEmptyRef | None = None
    score: Annotated[float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]
    quality: UnitInterval
    source_ref: NonEmptyRef
    identity_authority: Literal[False] = False


def _canonical_json(value: BaseModel | dict) -> bytes:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def profile_ref(profile: MrVisionProfile) -> str:
    if not isinstance(profile, MrVisionProfile):
        raise TypeError("profile must be MrVisionProfile")
    return "mrvision-profile:" + hashlib.sha256(_canonical_json(profile)).hexdigest()


def _normalize(vector: tuple[float, ...] | list[float]) -> tuple[float, ...]:
    if len(vector) < 2 or len(vector) > 8192:
        raise MrVisionError("embedding dimension must be between 2 and 8192")
    values = tuple(float(value) for value in vector)
    if any(not math.isfinite(value) for value in values):
        raise MrVisionError("embedding contains non-finite values")
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 0:
        raise MrVisionError("embedding must not be a zero vector")
    return tuple(value / norm for value in values)


def create_profile(*, now: datetime | None = None) -> MrVisionProfile:
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise MrVisionError("profile timestamp must be timezone-aware")
    return MrVisionProfile(
        profile_id="mrvision-" + uuid4().hex,
        revision=1,
        created_at=timestamp,
        updated_at=timestamp,
        enrollments=(),
        identity_authority=False,
        raw_pixels_persisted=False,
    )


def _mutation_timestamp(
    profile: MrVisionProfile,
    now: datetime | None,
    *,
    operation: str,
) -> datetime:
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise MrVisionError(f"{operation} timestamp must be timezone-aware")
    if timestamp < profile.updated_at:
        raise MrVisionError(f"{operation} timestamp cannot precede current revision")
    return timestamp


def enroll_embedding(
    profile: MrVisionProfile,
    *,
    kind: EnrollmentKind,
    label: str,
    vector: tuple[float, ...] | list[float],
    embedding_model_id: str,
    quality: float,
    source_ref: str,
    subject_ref: str | None = None,
    now: datetime | None = None,
) -> MrVisionProfile:
    normalized = _normalize(vector)
    timestamp = _mutation_timestamp(profile, now, operation="enrollment")

    signature = hashlib.sha256(
        _canonical_json(
            {
                "kind": kind,
                "label": label,
                "subject_ref": subject_ref,
                "embedding_model_id": embedding_model_id,
                "vector": normalized,
                "source_ref": source_ref,
            }
        )
    ).hexdigest()
    enrollment_id = "venr-" + signature[:32]

    existing = next(
        (item for item in profile.enrollments if item.enrollment_id == enrollment_id),
        None,
    )
    if existing is not None:
        return profile

    enrollment = VisualEnrollment(
        enrollment_id=enrollment_id,
        kind=kind,
        label=label,
        subject_ref=subject_ref,
        embedding_model_id=embedding_model_id,
        vector=normalized,
        quality=quality,
        source_ref=source_ref,
        enrolled_at=timestamp,
    )
    return profile.model_copy(
        update={
            "revision": profile.revision + 1,
            "parent_profile_ref": profile_ref(profile),
            "updated_at": timestamp,
            "enrollments": (*profile.enrollments, enrollment),
        }
    )


def revoke_enrollment(
    profile: MrVisionProfile,
    enrollment_id: str,
    *,
    reason: str,
    now: datetime | None = None,
) -> MrVisionProfile:
    if not isinstance(reason, str) or not reason.strip():
        raise MrVisionError("revocation reason must not be empty")
    timestamp = _mutation_timestamp(profile, now, operation="revocation")

    found = False
    changed = False
    updated: list[VisualEnrollment] = []
    for item in profile.enrollments:
        if item.enrollment_id != enrollment_id:
            updated.append(item)
            continue
        found = True
        if item.revoked:
            updated.append(item)
            continue
        changed = True
        updated.append(
            item.model_copy(
                update={"revoked": True, "revocation_reason": reason}
            )
        )

    if not found:
        raise MrVisionError("enrollment does not exist")
    if not changed:
        return profile

    return profile.model_copy(
        update={
            "revision": profile.revision + 1,
            "parent_profile_ref": profile_ref(profile),
            "updated_at": timestamp,
            "enrollments": tuple(updated),
        }
    )


def match_embedding(
    profile: MrVisionProfile,
    *,
    vector: tuple[float, ...] | list[float],
    embedding_model_id: str,
    kind: EnrollmentKind,
    threshold: float = 0.75,
    top_k: int = 5,
) -> tuple[RecognitionMatch, ...]:
    if not -1.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between -1 and 1")
    if not 1 <= top_k <= 32:
        raise ValueError("top_k must be between 1 and 32")
    query = _normalize(vector)

    matches: list[RecognitionMatch] = []
    for item in profile.enrollments:
        if item.revoked or item.kind != kind:
            continue
        if item.embedding_model_id != embedding_model_id:
            continue
        if len(item.vector) != len(query):
            continue
        score = float(sum(a * b for a, b in zip(item.vector, query, strict=True)))
        score = max(-1.0, min(1.0, score))
        if score < threshold:
            continue
        matches.append(
            RecognitionMatch(
                enrollment_id=item.enrollment_id,
                kind=item.kind,
                label=item.label,
                subject_ref=item.subject_ref,
                score=score,
                quality=item.quality,
                source_ref=item.source_ref,
                identity_authority=False,
            )
        )

    matches.sort(key=lambda item: (-item.score, -item.quality, item.enrollment_id))
    return tuple(matches[:top_k])


def _aesgcm_type():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise MrVisionCryptoUnavailable(
            'encrypted .mrvision files require VisionRig ".[profile]"'
        ) from exc
    return AESGCM


def generate_profile_key() -> bytes:
    return os.urandom(32)


def seal_profile(profile: MrVisionProfile, key: bytes) -> bytes:
    if not isinstance(key, bytes) or len(key) != 32:
        raise MrVisionError("profile key must be exactly 32 bytes")
    AESGCM = _aesgcm_type()
    nonce = os.urandom(12)
    reference = profile_ref(profile)
    aad = ("visionrig/mrvision-envelope/v1|" + reference).encode("ascii")
    ciphertext = AESGCM(key).encrypt(nonce, _canonical_json(profile), aad)
    envelope = MrVisionEnvelope(
        profile_ref=reference,
        nonce_b64=base64.b64encode(nonce).decode("ascii"),
        ciphertext_b64=base64.b64encode(ciphertext).decode("ascii"),
        identity_authority=False,
    )
    return _canonical_json(envelope)


def open_profile(blob: bytes, key: bytes) -> MrVisionProfile:
    if not isinstance(key, bytes) or len(key) != 32:
        raise MrVisionError("profile key must be exactly 32 bytes")
    AESGCM = _aesgcm_type()
    try:
        envelope = MrVisionEnvelope.model_validate_json(blob)
        nonce = base64.b64decode(envelope.nonce_b64, validate=True)
        ciphertext = base64.b64decode(envelope.ciphertext_b64, validate=True)
        aad = (
            "visionrig/mrvision-envelope/v1|" + envelope.profile_ref
        ).encode("ascii")
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
        profile = MrVisionProfile.model_validate_json(plaintext)
    except Exception as exc:
        raise MrVisionError("unable to authenticate/decrypt .mrvision profile") from exc
    if profile_ref(profile) != envelope.profile_ref:
        raise MrVisionError(".mrvision profile reference mismatch")
    return profile
