"""Latest-frame reader for a stateless HTTP MJPEG camera stream."""

from __future__ import annotations

import asyncio
import logging
import threading
import time

import numpy as np


LOGGER = logging.getLogger(__name__)
LatestFrame = tuple[np.ndarray, float]


class MjpegFrameSource:
    """Reads an MJPEG stream in one background thread and retains only its latest frame."""

    def __init__(self, *, name: str, stream_url: str, reconnect_interval_seconds: float = 1.0) -> None:
        self._name = name
        self._stream_url = stream_url
        self._reconnect_interval_seconds = reconnect_interval_seconds
        self._latest_frame: LatestFrame | None = None
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
            target=self._capture_loop, name=f"mjpeg-frame-{self._name}", daemon=True
        )
        self._thread.start()

    async def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            capture, self._capture = self._capture, None
        if capture is not None:
            release = getattr(capture, "release", None)
            if callable(release):
                release()
        thread, self._thread = self._thread, None
        if thread is not None:
            await asyncio.to_thread(thread.join, 2.0)
        self._connected = False
        self._latest_frame = None

    def get_latest_frame(self) -> LatestFrame | None:
        return self._latest_frame

    def is_connected(self) -> bool:
        return self._connected

    def get_last_error(self) -> str | None:
        return self._last_error

    def _capture_loop(self) -> None:
        import cv2

        while not self._stop_event.is_set():
            capture = cv2.VideoCapture(self._stream_url, cv2.CAP_FFMPEG)
            with self._lock:
                self._capture = capture
            if not capture.isOpened():
                self._set_disconnected("could not open MJPEG stream")
                self._release(capture)
                self._wait_before_retry()
                continue
            while not self._stop_event.is_set():
                ok, frame = capture.read()
                if not ok or frame is None or frame.size == 0:
                    self._set_disconnected("MJPEG stream ended")
                    break
                self._latest_frame = (frame, time.monotonic())
                self._connected = True
                self._last_error = None
            self._release(capture)
            if not self._stop_event.is_set():
                self._wait_before_retry()

    def _release(self, capture: object) -> None:
        with self._lock:
            if self._capture is capture:
                self._capture = None
        release = getattr(capture, "release", None)
        if callable(release):
            release()

    def _wait_before_retry(self) -> None:
        self._stop_event.wait(self._reconnect_interval_seconds)

    def _set_disconnected(self, error: str) -> None:
        was_connected = self._connected
        self._connected = False
        self._last_error = error
        self._latest_frame = None
        if was_connected:
            LOGGER.warning("MJPEG media 연결이 끊겼습니다.", extra={"camera": self._name})
