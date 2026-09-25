from visionrig.contracts import SourceDescriptor
from visionrig.embeddings import EmbeddingRecord, EmbeddingStore
from visionrig.pipeline import Frame, StageResult
from visionrig.profile import create_profile, enroll_embedding
from visionrig.recognition import PlaceRecognitionStage


def test_place_recognition_uses_full_frame_embedding_as_hint() -> None:
    profile = enroll_embedding(
        create_profile(),
        kind="place",
        label="living-room",
        subject_ref="place:home:living-room",
        vector=[0.6, 0.8],
        embedding_model_id="scene/v1",
        quality=0.9,
        source_ref="enrollment:place:1",
    )
    store = EmbeddingStore()
    store.put(
        EmbeddingRecord(
            source_id="cam",
            frame_sequence=4,
            subject_entity_id=None,
            model_id="scene/v1",
            vector=(0.6, 0.8),
        )
    )
    frame = Frame(
        source=SourceDescriptor(source_id="cam", source_type="camera"),
        sequence=4,
        payload=None,
    )
    result = PlaceRecognitionStage(
        profile,
        store,
        embedding_model_id="scene/v1",
        threshold=0.9,
    ).process(frame, StageResult())

    assert result.scene_label == "mrvision-place:place:home:living-room"
    assert result.scene_confidence == 1.0


def test_place_recognition_does_not_override_existing_scene_label() -> None:
    profile = enroll_embedding(
        create_profile(),
        kind="place",
        label="living-room",
        vector=[1.0, 0.0],
        embedding_model_id="scene/v1",
        quality=0.9,
        source_ref="enrollment:place:1",
    )
    result = PlaceRecognitionStage(
        profile,
        EmbeddingStore(),
        embedding_model_id="scene/v1",
    ).process(
        Frame(
            source=SourceDescriptor(source_id="cam", source_type="camera"),
            sequence=1,
            payload=None,
        ),
        StageResult(scene_label="upstream-scene", scene_confidence=0.7),
    )
    assert result.scene_label == "upstream-scene"
    assert result.scene_confidence == 0.7
