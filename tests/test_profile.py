from datetime import datetime, timezone

import pytest

from visionrig.profile import (
    MrVisionError,
    create_profile,
    enroll_embedding,
    generate_profile_key,
    match_embedding,
    open_profile,
    profile_ref,
    revoke_enrollment,
    seal_profile,
)


NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def enrolled():
    profile = create_profile(now=NOW)
    return enroll_embedding(
        profile,
        kind="face",
        label="known-person",
        subject_ref="person:test",
        vector=[3.0, 4.0],
        embedding_model_id="face-encoder/v1",
        quality=0.95,
        source_ref="enrollment:test:1",
        now=NOW,
    )


def test_enrollment_is_revisioned_and_normalized() -> None:
    initial = create_profile(now=NOW)
    profile = enrolled()
    assert profile.revision == initial.revision + 1
    assert profile.parent_profile_ref is not None
    assert profile.enrollments[0].vector == pytest.approx((0.6, 0.8))
    assert profile.identity_authority is False
    assert profile.raw_pixels_persisted is False


def test_exact_duplicate_enrollment_is_idempotent() -> None:
    profile = enrolled()
    replay = enroll_embedding(
        profile,
        kind="face",
        label="known-person",
        subject_ref="person:test",
        vector=[3.0, 4.0],
        embedding_model_id="face-encoder/v1",
        quality=0.95,
        source_ref="enrollment:test:1",
        now=NOW,
    )
    assert replay is profile


def test_match_returns_hint_without_identity_authority() -> None:
    profile = enrolled()
    matches = match_embedding(
        profile,
        vector=[0.61, 0.79],
        embedding_model_id="face-encoder/v1",
        kind="face",
        threshold=0.9,
    )
    assert len(matches) == 1
    assert matches[0].label == "known-person"
    assert matches[0].subject_ref == "person:test"
    assert matches[0].identity_authority is False


def test_revoked_enrollment_no_longer_matches() -> None:
    profile = enrolled()
    enrollment_id = profile.enrollments[0].enrollment_id
    revoked = revoke_enrollment(
        profile,
        enrollment_id,
        reason="operator revoked sample",
        now=NOW,
    )
    assert revoked.revision == profile.revision + 1
    assert revoked.enrollments[0].revoked is True
    assert match_embedding(
        revoked,
        vector=[0.6, 0.8],
        embedding_model_id="face-encoder/v1",
        kind="face",
    ) == ()


def test_encrypted_profile_round_trip_and_wrong_key_fail_closed() -> None:
    profile = enrolled()
    key = generate_profile_key()
    blob = seal_profile(profile, key)
    assert b"known-person" not in blob
    restored = open_profile(blob, key)
    assert restored == profile
    assert profile_ref(restored) == profile_ref(profile)

    with pytest.raises(MrVisionError):
        open_profile(blob, generate_profile_key())


def test_tamper_fails_closed() -> None:
    profile = enrolled()
    key = generate_profile_key()
    blob = bytearray(seal_profile(profile, key))
    blob[-8] ^= 1
    with pytest.raises(MrVisionError):
        open_profile(bytes(blob), key)
