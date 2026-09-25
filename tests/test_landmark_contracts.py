from visionrig.contracts import LandmarkObservation, VisualLandmark


def test_landmark_contract_is_typed_and_bounded() -> None:
    observation = LandmarkObservation(
        observation_id="pose-1",
        group="pose",
        landmarks=(
            VisualLandmark(
                name="nose",
                x=0.5,
                y=0.25,
                z=-0.1,
                confidence=0.9,
            ),
        ),
    )
    assert observation.landmarks[0].name == "nose"
