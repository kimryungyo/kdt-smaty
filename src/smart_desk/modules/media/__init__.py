"""카메라 발행과 MediaMTX RTSP 최신 프레임 입력을 제공한다."""

from smart_desk.modules.media.frame_source import LatestFrame, RtspFrameSource
from smart_desk.modules.media.publisher import CameraPublisher

__all__ = ["CameraPublisher", "LatestFrame", "RtspFrameSource"]
