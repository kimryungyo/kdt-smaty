"""실제 모델 전의 최소 detector adapter 경계."""

from __future__ import annotations

from math import acos, degrees
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from smart_desk.modules.vision.models import FaceBox, LowerDetection, PostureStatus, UpperDetection


class VisionDetector(Protocol):
    """CPU-bound 호출이며 VisionService가 executor에서 실행한다."""

    def detect_upper(self, frame: np.ndarray) -> UpperDetection: ...

    def detect_lower(self, frame: np.ndarray) -> LowerDetection: ...


class NoopVisionDetector:
    """실물 model/ROI가 확정되기 전 fail-closed 기본 adapter다."""

    def detect_upper(self, _frame: np.ndarray) -> UpperDetection:
        return UpperDetection(body_count=None)

    def detect_lower(self, _frame: np.ndarray) -> LowerDetection:
        return LowerDetection(count=None)


class CompositeVisionDetector:
    """Delegates each camera role to its independently configured adapter."""

    def __init__(self, upper: VisionDetector, lower: VisionDetector) -> None:
        self._upper, self._lower = upper, lower

    def detect_upper(self, frame: np.ndarray) -> UpperDetection:
        return self._upper.detect_upper(frame)

    def detect_lower(self, frame: np.ndarray) -> LowerDetection:
        return self._lower.detect_lower(frame)


class PresenceAndFaceUpperDetector:
    """상단 재실 인원과 얼굴 식별 근거를 독립 detector로 결합한다."""

    def __init__(self, presence: VisionDetector, faces: VisionDetector) -> None:
        self._presence, self._faces = presence, faces

    def detect_upper(self, frame: np.ndarray) -> UpperDetection:
        presence = self._presence.detect_upper(frame)
        faces = self._faces.detect_upper(frame)
        return UpperDetection(body_count=presence.body_count, face_boxes=faces.face_boxes)

    def detect_lower(self, _frame: np.ndarray) -> LowerDetection:
        return LowerDetection(count=None)


class OpenCvYuNetUpperDetector:
    """YuNet 얼굴 검출기. 재실 인원 수는 판단하지 않는다."""

    _ROW_WIDTH = 15

    def __init__(self, model_path: Path, *, score_threshold: float, nms_threshold: float,
                 min_face_size: int) -> None:
        if not model_path.is_file():
            raise FileNotFoundError(f"YuNet model not found: {model_path}")
        self._detector = cv2.FaceDetectorYN.create(
            str(model_path), "", (320, 320), score_threshold, nms_threshold, 5000
        )
        if self._detector is None:
            raise RuntimeError("Unable to create OpenCV YuNet detector")
        self._min_face_size = min_face_size

    def detect_upper(self, frame: np.ndarray) -> UpperDetection:
        if frame.ndim != 3 or frame.shape[0] <= 0 or frame.shape[1] <= 0:
            raise ValueError("YuNet frame must be a non-empty color image")
        self._detector.setInputSize((int(frame.shape[1]), int(frame.shape[0])))
        _status, rows = self._detector.detect(frame)
        if rows is None:
            return UpperDetection(body_count=None)
        rows = np.asarray(rows)
        if rows.ndim != 2 or rows.shape[1] != self._ROW_WIDTH or not np.isfinite(rows).all():
            raise ValueError("Malformed YuNet output")
        boxes: list[FaceBox] = []
        for row in rows:
            x, y, width, height = row[:4]
            landmarks = tuple((float(row[index]), float(row[index + 1])) for index in range(4, 14, 2))
            if (
                width <= 0 or height <= 0 or x < 0 or y < 0
                or x + width > frame.shape[1] or y + height > frame.shape[0]
                or row[14] < 0 or row[14] > 1
                or any(point_x < 0 or point_y < 0 or point_x >= frame.shape[1] or point_y >= frame.shape[0]
                       for point_x, point_y in landmarks)
            ):
                raise ValueError("Malformed YuNet row")
            if width < self._min_face_size or height < self._min_face_size:
                continue
            boxes.append(FaceBox(int(x), int(y), int(width), int(height), landmarks, float(row[14])))
        return UpperDetection(body_count=None, face_boxes=tuple(boxes))

    def detect_lower(self, _frame: np.ndarray) -> LowerDetection:
        return LowerDetection(count=None)


class OpenCvYoloPoseLowerDetector:
    """OpenCV DNN YOLO pose 하단 detector.

    동일 ONNX를 상단에 별도 인스턴스로 조립하면, pose keypoint 여부와 무관하게
    person confidence로 상체 재실 인원만 판정한다. 하단 인스턴스만 자세를 판정한다.
    """

    _OUTPUT_SHAPE = (1, 300, 57)

    def __init__(
        self,
        model_path: Path,
        *,
        input_size: int,
        min_person_confidence: float,
        min_hip_confidence: float,
        min_knee_ankle_confidence: float,
        decision_threshold: float,
    ) -> None:
        if not model_path.is_file():
            raise FileNotFoundError(f"Lower pose model not found: {model_path}")
        self._net = cv2.dnn.readNetFromONNX(str(model_path))
        self._input_size = input_size
        self._min_person_confidence = min_person_confidence
        self._min_hip_confidence = min_hip_confidence
        self._min_knee_ankle_confidence = min_knee_ankle_confidence
        self._decision_threshold = decision_threshold

    def detect_upper(self, frame: np.ndarray) -> UpperDetection:
        return UpperDetection(body_count=len(self._people(frame)))

    def detect_lower(self, frame: np.ndarray) -> LowerDetection:
        people = self._people(frame)
        count = len(people)
        if count != 1:
            return LowerDetection(count=count)
        keypoints = people[0, 6:].reshape(17, 3).astype(np.float32)
        _canvas, scale, pad_x, pad_y = self._letterbox(frame)
        keypoints[:, 0] = (keypoints[:, 0] - pad_x) / scale
        keypoints[:, 1] = (keypoints[:, 1] - pad_y) / scale
        posture = self._posture_from_keypoints(keypoints)
        return LowerDetection(count=1, posture=posture)

    def _people(self, frame: np.ndarray) -> np.ndarray:
        canvas, _scale, _pad_x, _pad_y = self._letterbox(frame)
        blob = cv2.dnn.blobFromImage(
            canvas, 1.0 / 255.0, (self._input_size, self._input_size), swapRB=True, crop=False
        )
        self._net.setInput(blob)
        output = self._net.forward()
        if not isinstance(output, np.ndarray) or output.shape != self._OUTPUT_SHAPE:
            raise ValueError(
                f"Unexpected lower pose model output shape: {getattr(output, 'shape', None)!r}; "
                f"expected {self._OUTPUT_SHAPE}"
            )
        return output[0][output[0, :, 4] >= self._min_person_confidence]

    def _letterbox(self, frame: np.ndarray) -> tuple[np.ndarray, float, int, int]:
        if frame.ndim != 3 or frame.shape[0] <= 0 or frame.shape[1] <= 0:
            raise ValueError("Lower pose frame must be a non-empty color image")
        height, width = frame.shape[:2]
        scale = min(self._input_size / width, self._input_size / height)
        resized_width, resized_height = round(width * scale), round(height * scale)
        pad_x = (self._input_size - resized_width) // 2
        pad_y = (self._input_size - resized_height) // 2
        canvas = np.full((self._input_size, self._input_size, 3), 114, dtype=np.uint8)
        resized = cv2.resize(frame, (resized_width, resized_height), interpolation=cv2.INTER_AREA)
        canvas[pad_y : pad_y + resized_height, pad_x : pad_x + resized_width] = resized
        return canvas, scale, pad_x, pad_y

    def _posture_from_keypoints(self, keypoints: np.ndarray) -> PostureStatus:
        extensions: list[float] = []
        for hip, knee, ankle in ((11, 13, 15), (12, 14, 16)):
            if (
                keypoints[hip, 2] < self._min_hip_confidence
                or keypoints[knee, 2] < self._min_knee_ankle_confidence
                or keypoints[ankle, 2] < self._min_knee_ankle_confidence
            ):
                continue
            angle = self._angle(keypoints[hip, :2], keypoints[knee, :2], keypoints[ankle, :2])
            if angle is None:
                continue
            extensions.append(max(0.0, min(1.0, (angle - 120.0) / 45.0)))
        if not extensions:
            return PostureStatus.UNKNOWN
        if min(extensions) >= self._decision_threshold:
            return PostureStatus.STANDING
        return PostureStatus.SITTING

    @staticmethod
    def _angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float | None:
        if not np.isfinite(np.concatenate((a, b, c))).all():
            return None
        ba, bc = a - b, c - b
        denominator = float(np.linalg.norm(ba) * np.linalg.norm(bc))
        if not np.isfinite(denominator) or denominator < 1e-7:
            return None
        cosine = float(np.dot(ba, bc)) / denominator
        if not np.isfinite(cosine):
            return None
        cosine = max(-1.0, min(1.0, cosine))
        return degrees(acos(cosine))
