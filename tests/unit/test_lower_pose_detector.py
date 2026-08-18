"""OpenCV DNN YOLO pose 하단 adapter의 frame 단위 fail-closed 판정 테스트."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from smart_desk.modules.vision import OpenCvYoloPoseLowerDetector, PostureStatus


class FakeNet:
    def __init__(self, output: np.ndarray) -> None:
        self.output = output
        self.input: np.ndarray | None = None

    def setInput(self, blob: np.ndarray) -> None:  # noqa: N802 - OpenCV API
        self.input = blob

    def forward(self) -> np.ndarray:
        return self.output


def make_detector(
    monkeypatch: pytest.MonkeyPatch, output: np.ndarray
) -> OpenCvYoloPoseLowerDetector:
    net = FakeNet(output)
    monkeypatch.setattr("cv2.dnn.readNetFromONNX", lambda _path: net)
    model = Path(__file__)
    return OpenCvYoloPoseLowerDetector(
        model, input_size=640, min_person_confidence=0.30,
        min_hip_confidence=0.08, min_knee_ankle_confidence=0.45,
        decision_threshold=0.52,
    )


def pose_output(*, confidences: tuple[float, ...] = (0.9,), legs: tuple[str, str] = ("bent", "bent")) -> np.ndarray:
    output = np.zeros((1, 300, 57), dtype=np.float32)
    for row_index, confidence in enumerate(confidences):
        row = output[0, row_index]
        row[4] = confidence
        for hip, knee, ankle, kind in ((11, 13, 15, legs[0]), (12, 14, 16, legs[1])):
            row[6 + hip * 3 : 6 + hip * 3 + 3] = (100, 100, 0.9)
            row[6 + knee * 3 : 6 + knee * 3 + 3] = (100, 200, 0.9)
            ankle_xy = (200, 200) if kind == "bent" else (100, 300)
            row[6 + ankle * 3 : 6 + ankle * 3 + 3] = (*ankle_xy, 0.9)
    return output


def test_lower_uses_highest_confidence_pose_without_using_people_count_for_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = np.zeros((640, 640, 3), dtype=np.uint8)
    assert make_detector(monkeypatch, pose_output(confidences=())).detect_lower(frame).count == 0
    assert make_detector(monkeypatch, pose_output()).detect_lower(frame).posture is PostureStatus.SITTING
    multiple = make_detector(monkeypatch, pose_output(confidences=(0.9, 0.8))).detect_lower(frame)
    assert multiple.count == 2 and multiple.posture is PostureStatus.SITTING


def test_same_pose_model_counts_upper_presence_without_requiring_visible_face(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = np.zeros((640, 640, 3), dtype=np.uint8)
    detector = make_detector(monkeypatch, pose_output(confidences=(0.9,)))
    assert detector.detect_upper(frame).body_count == 1


def test_upper_presence_threshold_excludes_low_confidence_person_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = np.zeros((640, 640, 3), dtype=np.uint8)
    output = pose_output(confidences=(0.59, 0.60))
    net = FakeNet(output)
    monkeypatch.setattr("cv2.dnn.readNetFromONNX", lambda _path: net)
    detector = OpenCvYoloPoseLowerDetector(
        Path(__file__), input_size=640, min_person_confidence=0.60,
        min_hip_confidence=0.08, min_knee_ankle_confidence=0.45,
        decision_threshold=0.52,
    )

    result = detector.detect_upper(frame)

    assert result.body_count == 1
    assert result.person_boxes[0].confidence == pytest.approx(0.60)


def test_debug_box_uses_end_to_end_xyxy_coordinates_like_sitting_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = np.zeros((640, 640, 3), dtype=np.uint8)
    output = pose_output()
    # ~/sitting의 YoloPoseEstimator와 같이 row[:4]는 left/top/right/bottom이다.
    output[0, 0, :4] = (100, 120, 300, 520)

    result = make_detector(monkeypatch, output).detect_upper(frame)

    assert result.person_boxes[0].x == 100
    assert result.person_boxes[0].y == 120
    assert result.person_boxes[0].width == 200
    assert result.person_boxes[0].height == 400


def test_leg_geometry_uses_most_bent_valid_side(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = np.zeros((640, 640, 3), dtype=np.uint8)
    assert make_detector(monkeypatch, pose_output(legs=("straight", "straight"))).detect_lower(frame).posture is PostureStatus.STANDING
    assert make_detector(monkeypatch, pose_output(legs=("straight", "bent"))).detect_lower(frame).posture is PostureStatus.SITTING


def test_one_valid_side_and_invalid_or_degenerate_joints(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = np.zeros((640, 640, 3), dtype=np.uint8)
    output = pose_output(legs=("straight", "bent"))
    output[0, 0, 6 + 12 * 3 + 2] = 0.01  # right hip is unavailable
    assert make_detector(monkeypatch, output).detect_lower(frame).posture is PostureStatus.STANDING

    output = pose_output()
    output[0, 0, 6 + 13 * 3 + 2] = 0.1
    output[0, 0, 6 + 14 * 3 + 2] = 0.1
    assert make_detector(monkeypatch, output).detect_lower(frame).posture is PostureStatus.UNKNOWN

    output = pose_output()
    output[0, 0, 6 + 11 * 3 : 6 + 11 * 3 + 2] = (100, 200)
    output[0, 0, 6 + 13 * 3 : 6 + 13 * 3 + 2] = (100, 200)
    output[0, 0, 6 + 15 * 3 : 6 + 15 * 3 + 2] = (100, 200)
    output[0, 0, 6 + 12 * 3 + 2] = 0.01
    assert make_detector(monkeypatch, output).detect_lower(frame).posture is PostureStatus.UNKNOWN

    output = pose_output()
    output[0, 0, 6 + 11 * 3] = np.nan
    output[0, 0, 6 + 12 * 3 + 2] = 0.01
    assert make_detector(monkeypatch, output).detect_lower(frame).posture is PostureStatus.UNKNOWN


def test_malformed_model_output_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    detector = make_detector(monkeypatch, np.zeros((1, 57), dtype=np.float32))
    with pytest.raises(ValueError, match="output shape"):
        detector.detect_lower(np.zeros((640, 640, 3), dtype=np.uint8))


def test_optional_reference_samples_regression() -> None:
    """개발자 로컬 참고 assets가 있을 때만 실행하며 저장소에는 요구하지 않는다."""
    root = Path.home() / "sitting"
    model = root / "models/yolo26n-pose.onnx"
    samples = root / "data/samples"
    manifest_path = samples / "manifest.json"
    if not model.is_file() or not manifest_path.is_file():
        pytest.skip("external lower-pose reference model and samples are not provisioned")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    detector = OpenCvYoloPoseLowerDetector(
        model, input_size=640, min_person_confidence=0.30,
        min_hip_confidence=0.08, min_knee_ankle_confidence=0.45,
        decision_threshold=0.52,
    )
    for label, expected_count, expected_posture in (
        ("sitting", 10, PostureStatus.SITTING),
        ("sitting_fullbody", 6, PostureStatus.SITTING),
        ("standing", 4, PostureStatus.STANDING),
        ("empty", 6, PostureStatus.UNKNOWN),
    ):
        results = [
            detector.detect_lower(cv2.imread(str(samples / label / filename)))
            for filename in manifest["labels"][label]
        ]
        assert len(results) == expected_count
        assert all(result.posture is expected_posture for result in results)
        assert all(result.count == (0 if label == "empty" else 1) for result in results)
