"""실제 모델 전의 최소 detector adapter 경계."""

from __future__ import annotations

from math import acos, degrees
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from smart_desk.modules.vision.models import (
    DetectionBox,
    FaceBox,
    LowerDetection,
    PoseDetection,
    PoseKeypoint,
    PostureStatus,
    UpperDetection,
)


class VisionDetector(Protocol):
    """CPU-bound 호출이며 VisionService가 executor에서 실행한다."""

    def detect_upper(self, frame: np.ndarray) -> UpperDetection: ...

    def detect_lower(self, frame: np.ndarray) -> LowerDetection: ...


class NoopVisionDetector:
    """실물 model이 확정되기 전 fail-closed 기본 adapter다."""

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
        return UpperDetection(
            body_count=presence.body_count,
            face_boxes=faces.face_boxes,
            person_boxes=presence.person_boxes,
        )

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
                # YuNet은 화면 경계에 걸린 얼굴에서 box/landmark 일부를 frame 밖으로
                # 낼 수 있다. 이는 신원용 얼굴 후보로는 불가하지만 상단 YOLO의 재실
                # 인원 판정까지 실패시킬 detector 오류는 아니다.
                continue
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
        people, scale, pad_x, pad_y = self._people(frame)
        return UpperDetection(
            body_count=len(people),
            person_boxes=tuple(
                self._box_from_row(person, frame, scale, pad_x, pad_y) for person in people
            ),
        )

    def detect_lower(self, frame: np.ndarray) -> LowerDetection:
        people, scale, pad_x, pad_y = self._people(frame)
        count = len(people)
        poses = tuple(
            self._pose_from_row(person, frame, scale, pad_x, pad_y) for person in people
        )
        if count == 0:
            return LowerDetection(count=count, pose_detections=poses)
        # 하단은 재실 인원수를 결정하지 않는다. 여러 사람이 보이더라도 모델 confidence가
        # 가장 높은 한 명을 자세 입력으로 선택해 주변 통행 때문에 AUTO를 막지 않는다.
        selected = max(poses, key=lambda pose: pose.box.confidence or 0.0)
        keypoints = np.array(
            [(keypoint.x, keypoint.y, keypoint.confidence) for keypoint in selected.keypoints],
            dtype=np.float32,
        )
        posture = self._posture_from_keypoints(keypoints)
        return LowerDetection(count=count, posture=posture, pose_detections=poses)

    def _people(self, frame: np.ndarray) -> tuple[np.ndarray, float, int, int]:
        canvas, scale, pad_x, pad_y = self._letterbox(frame)
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
        people = output[0][output[0, :, 4] >= self._min_person_confidence]
        # 사람 box/confidence가 깨진 경우에는 어느 좌표도 신뢰할 수 없으므로 detector
        # 오류로 처리한다. 단일 관절의 NaN은 자세를 UNKNOWN으로 만들 수는 있어도
        # 재실 count 자체를 없애서는 안 된다.
        if not np.isfinite(people[:, :6]).all():
            raise ValueError("Lower pose model returned non-finite person box")
        return people, scale, pad_x, pad_y

    @staticmethod
    def _clip(value: float, upper: int) -> float:
        return max(0.0, min(value, float(upper)))

    def _box_from_row(
        self, row: np.ndarray, frame: np.ndarray, scale: float, pad_x: int, pad_y: int
    ) -> DetectionBox:
        frame_height, frame_width = frame.shape[:2]
        # 이 end-to-end YOLO26 ONNX는 [left, top, right, bottom]을 반환한다.
        # center/width/height로 해석하면 debug box가 실제 사람보다 훨씬 커지고
        # 위치도 어긋난다. ~/sitting의 YoloPoseEstimator와 같은 역변환이다.
        left, top, right, bottom = row[:4]
        left = self._clip((left - pad_x) / scale, frame_width)
        top = self._clip((top - pad_y) / scale, frame_height)
        right = self._clip((right - pad_x) / scale, frame_width)
        bottom = self._clip((bottom - pad_y) / scale, frame_height)
        return DetectionBox(
            x=round(left), y=round(top), width=max(0, round(right - left)),
            height=max(0, round(bottom - top)), confidence=float(row[4]),
        )

    def _pose_from_row(
        self, row: np.ndarray, frame: np.ndarray, scale: float, pad_x: int, pad_y: int
    ) -> PoseDetection:
        frame_height, frame_width = frame.shape[:2]
        raw = row[6:].reshape(17, 3)
        points = tuple(
            PoseKeypoint(
                x=self._clip((point[0] - pad_x) / scale, frame_width),
                y=self._clip((point[1] - pad_y) / scale, frame_height),
                confidence=max(0.0, min(float(point[2]), 1.0)),
            )
            if np.isfinite(point).all()
            else PoseKeypoint(x=0.0, y=0.0, confidence=0.0)
            for point in raw
        )
        return PoseDetection(
            box=self._box_from_row(row, frame, scale, pad_x, pad_y), keypoints=points
        )

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
