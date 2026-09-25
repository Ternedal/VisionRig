import numpy as np

from visionrig.contracts import BoundingBox, SourceDescriptor, VisualEntity
from visionrig.embeddings import EmbeddingStore, EntityEmbeddingStage
from visionrig.pipeline import Frame, StageResult
from visionrig.profile import create_profile, enroll_embedding
from visionrig.recognition import ProfileRecognitionStage


class FakeEncoder:
    model_id = "visual/test-v1"

    def encode(self, image):
        assert image.shape[0] > 0
        assert image.shape[1] > 0
        return (0.6, 0.8)


def test_entity_embedding_and_profile_recognition_attach_hint_only() -> None:
    profile = enroll_embedding(
        create_profile(),
        kind="body",
        label="known-person",
        subject_ref="person:test",
        vector=[0.6, 0.8],
        embedding_model_id="visual/test-v1",
        quality=0.9,
        source_ref="enrollment:test",
    )
    source = SourceDescriptor(source_id="cam", source_type="camera")
    frame = Frame(
        source=source,
        sequence=4,
        payload=np.zeros((100, 100, 3), dtype=np.uint8),
    )
    person = VisualEntity(
        entity_id="person-1",
        kind="person",
        label="person",
        confidence=0.88,
        bbox=BoundingBox(x=0.1, y=0.1, width=0.5, height=0.8),
    )
    initial = StageResult(entities=(person,))
    store = EmbeddingStore()

    embedded = EntityEmbeddingStage(FakeEncoder(), store).process(frame, initial)
    recognized = ProfileRecognitionStage(
        profile,
        store,
        embedding_model_id="visual/test-v1",
        threshold=0.9,
    ).process(frame, embedded)

    entity = recognized.entities[0]
    assert entity.label == "person"
    assert entity.confidence == 0.88
    assert entity.identity_hint == "mrvision:person:test"


def test_model_mismatch_produces_no_hint() -> None:
    profile = enroll_embedding(
        create_profile(),
        kind="object",
        label="favorite-mug",
        vector=[0.6, 0.8],
        embedding_model_id="other-model/v1",
        quality=0.9,
        source_ref="enrollment:mug",
    )
    frame = Frame(
        source=SourceDescriptor(source_id="cam", source_type="camera"),
        sequence=1,
        payload=np.zeros((50, 50, 3), dtype=np.uint8),
    )
    entity = VisualEntity(
        entity_id="object-1",
        kind="object",
        label="cup",
        confidence=0.9,
        bbox=BoundingBox(x=0.0, y=0.0, width=1.0, height=1.0),
    )
    store = EmbeddingStore()
    current = StageResult(entities=(entity,))
    EntityEmbeddingStage(FakeEncoder(), store).process(frame, current)
    result = ProfileRecognitionStage(
        profile,
        store,
        embedding_model_id="visual/test-v1",
    ).process(frame, current)
    assert result.entities[0].identity_hint is None
