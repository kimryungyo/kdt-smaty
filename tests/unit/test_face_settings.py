import pytest
from pydantic import ValidationError

from smart_desk.config.settings import FaceSettings


def test_face_settings_default_calibration_candidates_are_stable() -> None:
    settings = FaceSettings()
    assert settings.detector_model_path is None
    assert settings.embedding_model_path is None
    assert settings.match_threshold == settings.pairwise_consistency_threshold == .363
    assert settings.enrollment_sample_interval_seconds == .5


@pytest.mark.parametrize("values", [
    {"min_brightness": 100, "max_brightness": 100},
    {"match_threshold": float("nan")},
    {"enrollment_sample_interval_seconds": 0},
])
def test_face_settings_rejects_invalid_quality_and_threshold_values(values) -> None:
    with pytest.raises(ValidationError):
        FaceSettings(**values)
