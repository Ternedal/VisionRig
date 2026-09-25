from datetime import datetime, timezone
from pathlib import Path

import pytest

from visionrig.profile import MrVisionError, create_profile
from visionrig.profile_builder import enroll_image_files
from visionrig.profile_cli import _profile_summary
from visionrig.profile_io import (
    ProfileLoadError,
    initialize_encrypted_profile,
    load_encrypted_profile,
)


NOW = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)


class _Encoder:
    model_id = "embed/test-v1"

    def encode(self, image):
        return (3.0, 4.0)


def _loader(path: Path):
    return {"sample": path.name}


def test_image_enrollment_creates_revisioned_derived_samples(tmp_path: Path) -> None:
    first = tmp_path / "front.jpg"
    second = tmp_path / "side.jpg"
    first.write_bytes(b"front image bytes")
    second.write_bytes(b"side image bytes")

    initial = create_profile(now=NOW)
    enrolled = enroll_image_files(
        initial,
        image_paths=[first, second],
        encoder=_Encoder(),
        kind="face",
        label="known-person",
        subject_ref="person:test",
        quality=0.9,
        image_loader=_loader,
        now=NOW,
    )

    assert enrolled.revision == initial.revision + 2
    assert len(enrolled.enrollments) == 2
    assert all(item.vector == pytest.approx((0.6, 0.8)) for item in enrolled.enrollments)
    assert all(item.embedding_model_id == "embed/test-v1" for item in enrolled.enrollments)
    assert all(item.source_ref.startswith("image-sha256:") for item in enrolled.enrollments)
    assert all(str(tmp_path) not in item.source_ref for item in enrolled.enrollments)
    assert enrolled.raw_pixels_persisted is False
    assert enrolled.identity_authority is False


def test_exact_image_reenrollment_is_idempotent(tmp_path: Path) -> None:
    sample = tmp_path / "sample.jpg"
    sample.write_bytes(b"same image")
    initial = create_profile(now=NOW)
    enrolled = enroll_image_files(
        initial,
        image_paths=[sample],
        encoder=_Encoder(),
        kind="body",
        label="body",
        image_loader=_loader,
        now=NOW,
    )
    replay = enroll_image_files(
        enrolled,
        image_paths=[sample],
        encoder=_Encoder(),
        kind="body",
        label="body",
        image_loader=_loader,
        now=NOW,
    )
    assert replay is enrolled


def test_enrollment_quality_and_image_count_are_bounded(tmp_path: Path) -> None:
    sample = tmp_path / "sample.jpg"
    sample.write_bytes(b"sample")
    profile = create_profile(now=NOW)

    with pytest.raises(MrVisionError, match="quality"):
        enroll_image_files(
            profile,
            image_paths=[sample],
            encoder=_Encoder(),
            kind="object",
            label="cup",
            quality=1.1,
            image_loader=_loader,
            now=NOW,
        )

    with pytest.raises(MrVisionError, match="at least one"):
        enroll_image_files(
            profile,
            image_paths=[],
            encoder=_Encoder(),
            kind="object",
            label="cup",
            image_loader=_loader,
            now=NOW,
        )


def test_profile_summary_does_not_expose_vectors(tmp_path: Path) -> None:
    sample = tmp_path / "sample.jpg"
    sample.write_bytes(b"sample")
    profile = enroll_image_files(
        create_profile(now=NOW),
        image_paths=[sample],
        encoder=_Encoder(),
        kind="place",
        label="office",
        image_loader=_loader,
        now=NOW,
    )
    summary = _profile_summary(profile)
    enrollment = summary["enrollments"][0]
    assert "vector" not in enrollment
    assert enrollment["vector_dimensions"] == 2
    assert str(sample) not in str(summary)


def test_initialize_encrypted_profile_creates_external_key_and_round_trips(
    tmp_path: Path,
) -> None:
    profile = create_profile(now=NOW)
    profile_path = tmp_path / "person.mrvision"
    key_path = tmp_path / "person.key"

    initialize_encrypted_profile(profile, profile_path, key_path)

    assert profile_path.is_file()
    assert key_path.is_file()
    assert len(key_path.read_bytes()) == 32
    assert load_encrypted_profile(profile_path, key_path) == profile

    with pytest.raises(ProfileLoadError, match="overwrite"):
        initialize_encrypted_profile(profile, profile_path, key_path)
