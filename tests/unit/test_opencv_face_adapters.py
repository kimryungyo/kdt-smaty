from datetime import UTC, datetime

import numpy as np
import pytest

from smart_desk.modules.identity.opencv import OpenCvSFaceEmbeddingExtractor
import smart_desk.modules.identity.opencv as sface_module
from smart_desk.modules.vision.detector import (
    CompositeVisionDetector,
    OpenCvYuNetUpperDetector,
    PresenceAndFaceUpperDetector,
)
import smart_desk.modules.vision.detector as yunet_module
from smart_desk.modules.vision.models import FaceBox, FreshFaceObservation, LowerDetection, UpperDetection


class YuNet:
    def __init__(self, rows):
        self.rows = rows
        self.size = None

    def setInputSize(self, size):
        self.size = size

    def detect(self, _frame):
        return 0, self.rows


def test_yunet_preserves_five_landmarks_without_claiming_presence(tmp_path, monkeypatch) -> None:
    model = tmp_path / "yunet.onnx"
    model.touch()
    fake = YuNet(np.array([[1, 2, 80, 90, 10, 11, 20, 21, 30, 31, 40, 41, 50, 51, .9]]))
    monkeypatch.setattr(yunet_module.cv2, "FaceDetectorYN", type("Factory", (), {"create": staticmethod(lambda *_args: fake)}))
    detector = OpenCvYuNetUpperDetector(model, score_threshold=.85, nms_threshold=.3, min_face_size=64)
    result = detector.detect_upper(np.zeros((120, 160, 3), dtype=np.uint8))
    assert result.body_count is None
    assert fake.size == (160, 120)
    assert result.face_boxes[0].landmarks == ((10.0, 11.0), (20.0, 21.0), (30.0, 31.0), (40.0, 41.0), (50.0, 51.0))


@pytest.mark.parametrize("rows", [np.empty((0, 15)), None])
def test_yunet_zero_rows_leaves_presence_unavailable(tmp_path, monkeypatch, rows) -> None:
    model = tmp_path / "yunet.onnx"
    model.touch()
    monkeypatch.setattr(yunet_module.cv2, "FaceDetectorYN", type("Factory", (), {"create": staticmethod(lambda *_args: YuNet(rows))}))
    detector = OpenCvYuNetUpperDetector(model, score_threshold=.85, nms_threshold=.3, min_face_size=64)
    assert detector.detect_upper(np.zeros((10, 10, 3), dtype=np.uint8)).body_count is None


@pytest.mark.parametrize("rows", [np.ones((1, 14)), np.full((1, 15), np.nan)])
def test_yunet_structurally_bad_output_fails_closed(tmp_path, monkeypatch, rows) -> None:
    model = tmp_path / "yunet.onnx"
    model.touch()
    monkeypatch.setattr(yunet_module.cv2, "FaceDetectorYN", type("Factory", (), {"create": staticmethod(lambda *_args: YuNet(rows))}))
    detector = OpenCvYuNetUpperDetector(model, score_threshold=.85, nms_threshold=.3, min_face_size=64)
    with pytest.raises(ValueError):
        detector.detect_upper(np.zeros((10, 10, 3), dtype=np.uint8))


@pytest.mark.parametrize(
    "row",
    [
        [-1, 0, 8, 8, 1, 1, 2, 1, 3, 3, 1, 5, 5, 5, .9],
        [1, 0, 12, 8, 1, 1, 2, 1, 3, 3, 1, 5, 5, 5, .9],
        [1, 1, 8, 8, 1, 1, 2, 1, 3, 3, 1, 5, 11, 5, .9],
    ],
)
def test_yunet_out_of_frame_face_is_ignored_not_detector_error(
    tmp_path, monkeypatch, row
) -> None:
    model = tmp_path / "yunet.onnx"
    model.touch()
    fake = YuNet(np.array([row]))
    monkeypatch.setattr(
        yunet_module.cv2,
        "FaceDetectorYN",
        type("Factory", (), {"create": staticmethod(lambda *_args: fake)}),
    )
    detector = OpenCvYuNetUpperDetector(
        model, score_threshold=.85, nms_threshold=.3, min_face_size=1
    )

    result = detector.detect_upper(np.zeros((10, 10, 3), dtype=np.uint8))

    assert result.body_count is None
    assert result.face_boxes == ()


class SFace:
    def __init__(self):
        self.row = None

    def alignCrop(self, _frame, row):
        self.row = row
        return np.zeros((112, 112, 3), dtype=np.uint8)

    def feature(self, _aligned):
        return np.arange(1, 129, dtype=np.float32)


def test_sface_aligns_yunet_row_and_l2_normalizes(tmp_path, monkeypatch) -> None:
    model = tmp_path / "sface.onnx"
    model.touch()
    fake = SFace()
    monkeypatch.setattr(sface_module.cv2, "FaceRecognizerSF", type("Factory", (), {"create": staticmethod(lambda *_args: fake)}))
    extractor = OpenCvSFaceEmbeddingExtractor(model, min_face_size=16, min_blur_variance=0,
                                                min_brightness=0, max_brightness=255)
    frame = np.random.default_rng(1).integers(0, 255, (100, 100, 3), dtype=np.uint8)
    box = FaceBox(10, 10, 50, 50, ((15, 15), (45, 15), (30, 30), (20, 45), (40, 45)), .9)
    vector = extractor.extract(FreshFaceObservation(frame, (box,), 1.0, datetime.now(UTC)))
    assert vector is not None and len(vector) == 128
    assert np.isclose(np.linalg.norm(vector), 1.0)
    assert fake.row.shape == (1, 15)
    assert fake.row[0, 4:14].tolist() == [15, 15, 45, 15, 30, 30, 20, 45, 40, 45]


def test_sface_rejects_missing_landmarks_before_storing_a_crop(tmp_path, monkeypatch) -> None:
    model = tmp_path / "sface.onnx"
    model.touch()
    monkeypatch.setattr(sface_module.cv2, "FaceRecognizerSF", type("Factory", (), {"create": staticmethod(lambda *_args: SFace())}))
    extractor = OpenCvSFaceEmbeddingExtractor(model, min_face_size=16, min_blur_variance=0,
                                                min_brightness=0, max_brightness=255)
    observation = FreshFaceObservation(np.ones((100, 100, 3), dtype=np.uint8), (FaceBox(0, 0, 30, 30),), 1.0, datetime.now(UTC))
    assert extractor.extract(observation) is None


@pytest.mark.parametrize(
    "box",
    [
        FaceBox(0, 0, 30, 30, ((1, 1), (2, 2), (3, 3), (4, 4), (101, 5)), .9),
        FaceBox(0, 0, 30, 30, ((1, 1), (2, 2), (3, 3), (4, 4), (5, 5)), 1.1),
    ],
)
def test_sface_rejects_out_of_frame_landmarks_and_confidence(tmp_path, monkeypatch, box) -> None:
    model = tmp_path / "sface.onnx"
    model.touch()
    monkeypatch.setattr(sface_module.cv2, "FaceRecognizerSF", type("Factory", (), {"create": staticmethod(lambda *_args: SFace())}))
    extractor = OpenCvSFaceEmbeddingExtractor(model, min_face_size=16, min_blur_variance=0,
                                                min_brightness=0, max_brightness=255)
    observation = FreshFaceObservation(np.ones((100, 100, 3), dtype=np.uint8), (box,), 1.0, datetime.now(UTC))
    assert extractor.extract(observation) is None


def test_composite_delegates_camera_roles_without_cross_inference() -> None:
    class Upper:
        def detect_upper(self, _frame): return UpperDetection(0)
        def detect_lower(self, _frame): raise AssertionError("wrong adapter")

    class Lower:
        def detect_upper(self, _frame): raise AssertionError("wrong adapter")
        def detect_lower(self, _frame): return LowerDetection(0)

    detector = CompositeVisionDetector(Upper(), Lower())
    frame = np.zeros((1, 1, 3), dtype=np.uint8)
    assert detector.detect_upper(frame).body_count == 0
    assert detector.detect_lower(frame).count == 0


def test_upper_presence_uses_pose_person_count_and_preserves_yunet_faces(monkeypatch) -> None:
    class Person:
        def detect_upper(self, _frame): return UpperDetection(1)
        def detect_lower(self, _frame): raise AssertionError("wrong role")

    class Faces:
        def detect_upper(self, _frame):
            return UpperDetection(None, (FaceBox(1, 2, 3, 4),))
        def detect_lower(self, _frame): raise AssertionError("wrong role")

    result = PresenceAndFaceUpperDetector(Person(), Faces()).detect_upper(
        np.zeros((1, 1, 3), dtype=np.uint8)
    )
    assert result.body_count == result.count == 1
    assert result.face_boxes == (FaceBox(1, 2, 3, 4),)


def test_face_boxes_never_promote_presence_without_body_detector() -> None:
    result = UpperDetection(None, (FaceBox(1, 2, 3, 4),))
    assert result.count is None
