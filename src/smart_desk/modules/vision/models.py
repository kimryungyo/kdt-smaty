"""Vision 내부 상태와 HTTP에 노출할 불변 관측 모델."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import numpy as np
from pydantic import BaseModel, ConfigDict


def _to_camel(field_name: str) -> str:
    first, *rest = field_name.split("_")
    return first + "".join(part.capitalize() for part in rest)


class CameraStatus(StrEnum):
    OFFLINE = "OFFLINE"
    ONLINE = "ONLINE"
    STALE = "STALE"
    ERROR = "ERROR"


class PresenceStatus(StrEnum):
    PRESENT_SINGLE = "PRESENT_SINGLE"
    VACANT = "VACANT"
    MULTIPLE = "MULTIPLE"
    UNKNOWN = "UNKNOWN"


class PostureStatus(StrEnum):
    SITTING = "SITTING"
    STANDING = "STANDING"
    UNKNOWN = "UNKNOWN"


class IdentityStatus(StrEnum):
    """Task 05가 채울 신원 상태 축의 안정된 공개 enum이다."""

    MATCHED = "MATCHED"
    UNKNOWN_FACE = "UNKNOWN_FACE"
    AMBIGUOUS = "AMBIGUOUS"
    NO_FACE = "NO_FACE"
    UNKNOWN = "UNKNOWN"


class BlockCode(StrEnum):
    UPPER_CAMERA_UNAVAILABLE = "UPPER_CAMERA_UNAVAILABLE"
    LOWER_CAMERA_UNAVAILABLE = "LOWER_CAMERA_UNAVAILABLE"
    UPPER_FRAME_STALE = "UPPER_FRAME_STALE"
    LOWER_FRAME_STALE = "LOWER_FRAME_STALE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_ERROR = "MODEL_ERROR"
    MULTIPLE_PEOPLE = "MULTIPLE_PEOPLE"
    COUNT_MISMATCH = "COUNT_MISMATCH"
    CAMERA_TIMESTAMP_MISMATCH = "CAMERA_TIMESTAMP_MISMATCH"
    POSTURE_UNASSOCIATED = "POSTURE_UNASSOCIATED"
    PRESENCE_NOT_SINGLE = "PRESENCE_NOT_SINGLE"
    POSTURE_UNKNOWN = "POSTURE_UNKNOWN"


@dataclass(frozen=True, slots=True)
class FaceBox:
    """상단 detector가 한 번 만든 얼굴 영역이다. 일반 API에는 노출하지 않는다."""

    x: int
    y: int
    width: int
    height: int
    landmarks: tuple[tuple[float, float], ...] = ()
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class DetectionBox:
    """메모리에만 보관하는 사람 detector 영역. Vision debug 화면 전용이다."""

    x: int
    y: int
    width: int
    height: int
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class PoseKeypoint:
    """원본 frame 좌표계의 pose 관절 하나다."""

    x: float
    y: float
    confidence: float


@dataclass(frozen=True, slots=True)
class PoseDetection:
    """한 사람의 box와 17개 COCO pose 관절이다."""

    box: DetectionBox
    keypoints: tuple[PoseKeypoint, ...]


@dataclass(frozen=True, slots=True)
class UpperDetection:
    body_count: int | None
    face_boxes: tuple[FaceBox, ...] = ()
    person_boxes: tuple[DetectionBox, ...] = ()

    @property
    def count(self) -> int | None:
        # 재실 판단은 상단 몸체 detector만 책임진다. 얼굴은 프로필 식별의 보조
        # 입력이므로, 얼굴이 검출됐다는 이유만으로 사람이 카메라 화각에 있다고
        # 가정하지 않는다.
        return self.body_count


@dataclass(frozen=True, slots=True)
class LowerDetection:
    count: int | None
    posture: PostureStatus = PostureStatus.UNKNOWN
    pose_detections: tuple[PoseDetection, ...] = ()


@dataclass(frozen=True, slots=True)
class FreshFaceObservation:
    """Task 05 전용 fresh 얼굴 결과 경계. DB/API로 raw image를 넘기지 않는다."""

    frame: np.ndarray
    boxes: tuple[FaceBox, ...]
    captured_monotonic: float
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class CameraObservation:
    connected: bool
    captured_monotonic: float | None
    observed_monotonic: float | None
    observed_at: datetime | None
    error: str | None
    count: int | None = None
    posture: PostureStatus = PostureStatus.UNKNOWN
    face_observation: FreshFaceObservation | None = None
    detector_error: bool = False
    frame_width: int | None = None
    frame_height: int | None = None
    person_boxes: tuple[DetectionBox, ...] = ()
    pose_detections: tuple[PoseDetection, ...] = ()
    # 메모리에만 존재하며 /api/vision/debug/frame 에서만 JPEG로 반환한다.
    # 일반 상태 API나 DB/세션에는 절대로 raw image를 싣지 않는다.
    debug_frame: np.ndarray | None = None


@dataclass(frozen=True, slots=True)
class VisionSnapshot:
    upper: CameraObservation
    lower: CameraObservation
    raw_presence: PresenceStatus
    stable_presence: PresenceStatus
    raw_posture: PostureStatus
    stable_posture: PostureStatus
    presence_candidate_since: datetime | None
    posture_candidate_since: datetime | None
    usable: bool
    reason_codes: tuple[BlockCode, ...]


class VisionApiModel(BaseModel):
    """일반 HTTP Vision 응답은 기존 계약과 같이 camelCase를 쓴다."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        frozen=True,
        serialize_by_alias=True,
        validate_by_alias=True,
        validate_by_name=True,
    )


class CameraStatusResponse(VisionApiModel):
    status: CameraStatus
    observed_at: datetime | None = None
    expires_at: datetime | None = None
    age_seconds: float | None = None
    error: str | None = None


class IdentityResponse(VisionApiModel):
    """Task 04는 신원을 판정하지 않으므로 UNKNOWN만 반환한다."""

    status: IdentityStatus = IdentityStatus.UNKNOWN
    profile_id: str | None = None
    observed_at: datetime | None = None
    expires_at: datetime | None = None


class PresenceResponse(VisionApiModel):
    raw_status: PresenceStatus
    status: PresenceStatus
    upper_count: int | None = None
    lower_count: int | None = None
    observed_at: datetime | None = None
    expires_at: datetime | None = None


class PostureResponse(VisionApiModel):
    raw_status: PostureStatus
    status: PostureStatus
    candidate_since: datetime | None = None
    observed_at: datetime | None = None
    expires_at: datetime | None = None


class AssociationResponse(VisionApiModel):
    usable: bool
    reason_codes: list[BlockCode]


class VisionStatusResponse(VisionApiModel):
    cameras: dict[str, CameraStatusResponse]
    identity: IdentityResponse
    presence: PresenceResponse
    posture: PostureResponse
    association: AssociationResponse


class DebugBoxResponse(VisionApiModel):
    x: int
    y: int
    width: int
    height: int
    confidence: float | None = None


class DebugKeypointResponse(VisionApiModel):
    x: float
    y: float
    confidence: float


class DebugPoseResponse(VisionApiModel):
    box: DebugBoxResponse
    keypoints: list[DebugKeypointResponse]


class VisionDebugCameraResponse(VisionApiModel):
    observed_at: datetime | None = None
    frame_width: int | None = None
    frame_height: int | None = None
    person_boxes: list[DebugBoxResponse] = []
    face_boxes: list[DebugBoxResponse] = []
    pose_detections: list[DebugPoseResponse] = []
    detector_error: bool = False
    error: str | None = None
    frame_available: bool = False


class VisionDebugResponse(VisionApiModel):
    """디버그용 geometry만 노출한다. 이미지 bytes는 별도 endpoint로 반환한다."""

    cameras: dict[str, VisionDebugCameraResponse]
