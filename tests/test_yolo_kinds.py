from visionrig.yolo_onnx import entity_kind_for_label


def test_detector_kind_defaults_and_explicit_mapping() -> None:
    assert entity_kind_for_label("person") == "person"
    assert entity_kind_for_label("cup") == "object"
    assert entity_kind_for_label("face", {"face": "face"}) == "face"
    assert entity_kind_for_label("hand", {"hand": "hand"}) == "hand"
