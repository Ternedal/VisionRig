from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from visionrig.contracts import (
    DepthObservation,
    InfraredObservation,
    PerceptionEvent,
    SourceDescriptor,
    VisualEntity,
)


def test_confidence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        VisualEntity(entity_id="x", kind="object", label="cup", confidence=1.1)


def test_event_is_non_authoritative() -> None:
    event = PerceptionEvent(
        event_id="e1",
        observed_at=datetime.now(timezone.utc),
        source=SourceDescriptor(source_id="cam-1", source_type="camera"),
        frame_sequence=1,
    )
    assert event.production_authority is False


def test_metric_depth_must_be_positive_and_finite() -> None:
    observation = DepthObservation(
        subject_entity_id="person-1",
        relative_depth=0.5,
        distance_m=2.25,
        confidence=0.9,
        method="hardware-depth",
    )
    assert observation.distance_m == 2.25

    with pytest.raises(ValidationError):
        DepthObservation(
            subject_entity_id="person-1",
            relative_depth=0.5,
            distance_m=0.0,
            method="hardware-depth",
        )


def test_infrared_observation_is_bounded_and_summary_only() -> None:
    observation = InfraredObservation(
        mean_intensity=0.4,
        contrast=0.2,
        hotspot_fraction=0.1,
        sample_count=217088,
    )
    assert observation.method == "kinect-v2-infrared-summary"

    with pytest.raises(ValidationError):
        InfraredObservation(
            mean_intensity=1.1,
            contrast=0.2,
            hotspot_fraction=0.1,
            sample_count=217088,
        )

    with pytest.raises(ValidationError):
        InfraredObservation.model_validate(
            {
                "mean_intensity": 0.4,
                "contrast": 0.2,
                "hotspot_fraction": 0.1,
                "sample_count": 217088,
                "raw": [1, 2, 3],
            }
        )
