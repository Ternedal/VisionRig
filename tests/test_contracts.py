from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from visionrig.contracts import SourceDescriptor, VisualEntity, PerceptionEvent


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
