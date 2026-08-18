"""카메라 발행과 최신 프레임 입력을 제공한다."""

from smart_desk.modules.media.mjpeg import MjpegFrameSource
from smart_desk.modules.media.webrtc import LatestFrame, WebRtcCameraPublisher, WebRtcFrameSource

__all__ = ["LatestFrame", "MjpegFrameSource", "WebRtcCameraPublisher", "WebRtcFrameSource"]
