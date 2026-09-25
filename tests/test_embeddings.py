from visionrig.contracts import SourceDescriptor
from visionrig.embeddings import EmbeddingStage, EmbeddingStore
from visionrig.pipeline import Frame, StageResult


class FakeEncoder:
    model_id = "fake/v1"

    def encode(self, image):
        return (0.6, 0.8)


def test_embedding_stage_keeps_vector_out_of_event_contract() -> None:
    store = EmbeddingStore(capacity=2)
    stage = EmbeddingStage(FakeEncoder(), store)
    frame = Frame(
        source=SourceDescriptor(source_id="camera", source_type="camera"),
        sequence=7,
        payload=object(),
    )
    result = stage.process(frame, StageResult())
    assert result == StageResult()
    record = store.get("camera", 7, model_id="fake/v1")
    assert record is not None
    assert record.vector == (0.6, 0.8)


def test_embedding_store_is_bounded() -> None:
    store = EmbeddingStore(capacity=1)
    stage = EmbeddingStage(FakeEncoder(), store)
    source = SourceDescriptor(source_id="camera", source_type="camera")
    stage.process(Frame(source=source, sequence=1, payload=None), StageResult())
    stage.process(Frame(source=source, sequence=2, payload=None), StageResult())
    assert len(store) == 1
    assert store.get("camera", 1, model_id="fake/v1") is None
