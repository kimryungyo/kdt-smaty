"""책상 상단 V4L2 카메라에서 최신 MJPEG 프레임 한 장만 유지한다."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import threading
import time
from typing import Any


LOGGER = logging.getLogger(__name__)
CaptureFactory = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    """Realtime에 바로 첨부할 수 있는 한 시점의 압축 JPEG다."""

    jpeg: bytes
    captured_at_monotonic: float
    captured_at_epoch: float
    width: int
    height: int

    def age_seconds(self, *, now: float | None = None) -> float:
        current = time.monotonic() if now is None else now
        return max(0.0, current - self.captured_at_monotonic)

    @property
    def captured_at(self) -> str:
        return datetime.fromtimestamp(self.captured_at_epoch, tz=timezone.utc).isoformat()


def _open_v4l2_capture(
    device: str,
    *,
    input_format: str,
    width: int,
    height: int,
    fps: int,
) -> Any:
    try:
        import av
    except ImportError as error:  # pragma: no cover - package dependency boundary
        raise RuntimeError("PyAV is required for workspace camera capture") from error
    return av.open(
        device,
        format="v4l2",
        options={
            "input_format": input_format,
            "video_size": f"{width}x{height}",
            "framerate": str(fps),
        },
    )


def _extract_jpeg(packet: object) -> bytes | None:
    payload = bytes(packet)
    start = payload.find(b"\xff\xd8")
    end = payload.rfind(b"\xff\xd9")
    if start < 0 or end < start:
        return None
    return payload[start : end + 2]


def _jpeg_dimensions(jpeg: bytes) -> tuple[int, int] | None:
    """SOF marker만 읽어 JPEG를 디코딩하지 않고 실제 크기를 얻는다."""

    sof_markers = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }
    offset = 2
    while offset + 3 < len(jpeg):
        if jpeg[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(jpeg) and jpeg[offset] == 0xFF:
            offset += 1
        if offset >= len(jpeg):
            return None
        marker = jpeg[offset]
        offset += 1
        if marker in {0x01, 0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(jpeg):
            return None
        length = int.from_bytes(jpeg[offset : offset + 2], "big")
        if length < 2 or offset + length > len(jpeg):
            return None
        if marker in sof_markers and length >= 7:
            height = int.from_bytes(jpeg[offset + 3 : offset + 5], "big")
            width = int.from_bytes(jpeg[offset + 5 : offset + 7], "big")
            return (width, height) if width > 0 and height > 0 else None
        offset += length
    return None


class WorkspaceCameraSource:
    """카메라를 계속 열어 두고 압축된 최신 프레임 하나만 교체한다."""

    def __init__(
        self,
        *,
        device: str,
        input_format: str,
        width: int,
        height: int,
        fps: int,
        reconnect_interval_seconds: float = 1.0,
        capture_factory: CaptureFactory = _open_v4l2_capture,
    ) -> None:
        self._device = device
        self._input_format = input_format
        self._width = width
        self._height = height
        self._fps = fps
        self._reconnect_interval_seconds = reconnect_interval_seconds
        self._capture_factory = capture_factory
        self._latest: WorkspaceSnapshot | None = None
        self._connected = False
        self._last_error: str | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._capture: object | None = None
        self._thread: threading.Thread | None = None

    async def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="workspace-camera",
            daemon=True,
        )
        self._thread.start()

    async def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            capture, self._capture = self._capture, None
        self._close(capture)
        thread, self._thread = self._thread, None
        if thread is not None:
            await asyncio.to_thread(thread.join, 2.0)
        with self._lock:
            self._latest = None
            self._connected = False

    def get_latest_snapshot(self) -> WorkspaceSnapshot | None:
        with self._lock:
            return self._latest

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def get_last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def _capture_loop(self) -> None:
        while not self._stop_event.is_set():
            capture: object | None = None
            try:
                capture = self._capture_factory(
                    self._device,
                    input_format=self._input_format,
                    width=self._width,
                    height=self._height,
                    fps=self._fps,
                )
                with self._lock:
                    stopping = self._stop_event.is_set()
                    if not stopping:
                        self._capture = capture
                if stopping:
                    self._close(capture)
                    return
                for packet in self._packets(capture):
                    if self._stop_event.is_set():
                        break
                    jpeg = _extract_jpeg(packet)
                    if jpeg is None:
                        continue
                    width, height = _jpeg_dimensions(jpeg) or (self._width, self._height)
                    snapshot = WorkspaceSnapshot(
                        jpeg=jpeg,
                        captured_at_monotonic=time.monotonic(),
                        captured_at_epoch=time.time(),
                        width=width,
                        height=height,
                    )
                    with self._lock:
                        self._latest = snapshot
                        self._connected = True
                        self._last_error = None
                if not self._stop_event.is_set():
                    self._set_disconnected("workspace_camera_stream_ended")
            except Exception as error:
                self._set_disconnected(type(error).__name__)
            finally:
                with self._lock:
                    if self._capture is capture:
                        self._capture = None
                self._close(capture)
            if not self._stop_event.is_set():
                self._stop_event.wait(self._reconnect_interval_seconds)

    @staticmethod
    def _packets(capture: object) -> Iterator[object]:
        demux = getattr(capture, "demux")
        return iter(demux(video=0))

    @staticmethod
    def _close(capture: object | None) -> None:
        if capture is None:
            return
        close = getattr(capture, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                LOGGER.debug("책상 카메라 입력 종료 중 오류를 무시합니다.", exc_info=True)

    def _set_disconnected(self, error: str) -> None:
        with self._lock:
            was_connected = self._connected
            self._connected = False
            self._last_error = error
        if was_connected:
            LOGGER.warning(
                "책상 카메라 입력이 끊겼습니다.",
                extra={"component": "media.workspace", "event": "camera_disconnected"},
            )
