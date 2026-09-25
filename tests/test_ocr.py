from visionrig.ocr import _entities_from_tesseract


def test_tesseract_data_becomes_normalized_text_entities() -> None:
    entities = _entities_from_tesseract(
        {
            "text": ["", "Hello"],
            "conf": ["-1", "92"],
            "left": [0, 10],
            "top": [0, 20],
            "width": [0, 50],
            "height": [0, 20],
        },
        image_width=200,
        image_height=100,
        min_confidence=0.5,
    )
    assert len(entities) == 1
    entity = entities[0]
    assert entity.kind == "text"
    assert entity.label == "Hello"
    assert entity.confidence == 0.92
    assert entity.bbox is not None
    assert entity.bbox.x == 0.05
