"""최신 frame 기반 Vision 관측 공개 경계."""

from smart_desk.core.container import get_container
from smart_desk.modules.vision.detector import (
    CompositeVisionDetector,
    PresenceAndFaceUpperDetector,
    OpenCvYuNetUpperDetector,
    OpenCvYoloPoseLowerDetector,
    NoopVisionDetector,
    VisionDetector,
)
from smart_desk.modules.vision.models import (
    BlockCode,
    DetectionBox,
    FaceBox,
    FreshFaceObservation,
    IdentityStatus,
    LowerDetection,
    PoseDetection,
    PoseKeypoint,
    PostureStatus,
    PresenceStatus,
    UpperDetection,
    VisionSnapshot,
)
from smart_desk.modules.vision.service import VisionService
from smart_desk.modules.vision.remote import RemoteVisionService


def get_vision() -> VisionService | RemoteVisionService:
    vision = get_container().vision
    if vision is None:
        raise RuntimeError("Vision service가 조립되지 않았습니다.")
    return vision


__all__ = [
    "BlockCode", "CompositeVisionDetector", "PresenceAndFaceUpperDetector", "DetectionBox", "FaceBox", "FreshFaceObservation", "IdentityStatus", "LowerDetection", "PoseDetection", "PoseKeypoint",
    "OpenCvYuNetUpperDetector", "OpenCvYoloPoseLowerDetector", "NoopVisionDetector", "PostureStatus",
    "PresenceStatus", "UpperDetection", "VisionDetector", "VisionService",
    "VisionSnapshot", "get_vision",
]
