"""최신 frame 기반 Vision 관측 공개 경계."""

from smart_desk.core.container import get_container
from smart_desk.modules.vision.detector import NoopVisionDetector, VisionDetector
from smart_desk.modules.vision.models import (
    BlockCode,
    FaceBox,
    FreshFaceObservation,
    IdentityStatus,
    LowerDetection,
    PostureStatus,
    PresenceStatus,
    UpperDetection,
    VisionSnapshot,
)
from smart_desk.modules.vision.service import VisionService


def get_vision() -> VisionService:
    vision = get_container().vision
    if vision is None:
        raise RuntimeError("Vision service가 조립되지 않았습니다.")
    return vision


__all__ = [
    "BlockCode", "FaceBox", "FreshFaceObservation", "IdentityStatus", "LowerDetection",
    "NoopVisionDetector", "PostureStatus", "PresenceStatus", "UpperDetection",
    "VisionDetector", "VisionService", "VisionSnapshot", "get_vision",
]
