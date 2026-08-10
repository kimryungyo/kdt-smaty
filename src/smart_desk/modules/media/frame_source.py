"""MediaMTX RTSP 경로에서 최신 프레임 하나를 유지한다."""

from __future__ import annotations

import asyncio
import logging
import threading
import time

import cv2
import numpy as np


LOGGER = logging.getLogger(__name__)
OPEN_TIMEOUT_MILLISECONDS = 3_000
READ_TIMEOUT_MILLISECONDS = 3_000
THREAD_JOIN_TIMEOUT_SECONDS = 5.0
LatestFrame = tuple[np.ndarray, float]


class RtspFrameSource:
    """RTSP URL 하나를 전용 thread에서 읽어 최신 프레임만 보관한다."""

    def __init__(
        self,
        *,
        name: str,
        rtsp_url: str,
        reconnect_interval_seconds: float = 1.0,
    ) -> None:
        self._name = name
        self._rtsp_url = rtsp_url
        self._reconnect_interval_seconds = reconnect_interval_seconds
        self._state_lock = threading.Lock()
        self._latest_frame: LatestFrame | None = None
        self._connected = False
        self._last_error: str | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    async def start(self) -> None:
        """RTSP reader thread 하나를 시작하고 첫 연결은 background에서 재시도한다."""

        thread = self._thread
        if thread is not None and thread.is_alive():
            return

        self._stop_event = threading.Event()
        with self._state_lock:
            self._latest_frame = None
            self._connected = False
            self._last_error = None
        thread = threading.Thread(
            target=self._run,
            args=(self._stop_event,),
            name=f"rtsp-frame-source-{self._name}",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    async def stop(self) -> None:
        """reader에 종료를 요청하고 유한 시간만 기다린다."""

        self._stop_event.set()
        with self._state_lock:
            self._latest_frame = None
            self._connected = False

        thread = self._thread
        if thread is None:
            return
        await asyncio.to_thread(thread.join, THREAD_JOIN_TIMEOUT_SECONDS)
        if not thread.is_alive():
            self._thread = None

    def get_latest_frame(self) -> LatestFrame | None:
        """가장 최근의 이미지와 capture 시각 또는 현재 frame 없음을 반환한다."""

        with self._state_lock:
            return self._latest_frame

    def is_connected(self) -> bool:
        """현재 RTSP reader가 frame을 읽을 수 있는 연결 상태인지 반환한다."""

        with self._state_lock:
            return self._connected

    def get_last_error(self) -> str | None:
        """마지막 연결 또는 read 오류 문자열을 반환한다."""

        with self._state_lock:
            return self._last_error

    def _run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            capture = self._open_capture()
            if capture is None:
                if stop_event.wait(self._reconnect_interval_seconds):
                    break
                continue

            self._set_connected()
            try:
                while not stop_event.is_set():
                    ok, frame = capture.read()
                    if not ok or frame is None:
                        self._set_disconnected("RTSP stream read failed.")
                        break
                    with self._state_lock:
                        self._latest_frame = (frame, time.monotonic())
                        self._connected = True
                        self._last_error = None
            except Exception as error:
                self._set_disconnected(self._format_error(error))
            finally:
                capture.release()

            if not stop_event.is_set() and stop_event.wait(self._reconnect_interval_seconds):
                break

        with self._state_lock:
            self._latest_frame = None
            self._connected = False

    def _open_capture(self):
        try:
            capture = cv2.VideoCapture(
                self._rtsp_url,
                cv2.CAP_FFMPEG,
                (
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
                    OPEN_TIMEOUT_MILLISECONDS,
                    cv2.CAP_PROP_READ_TIMEOUT_MSEC,
                    READ_TIMEOUT_MILLISECONDS,
                ),
            )
            if capture.isOpened():
                return capture
            capture.release()
            self._set_disconnected("RTSP stream connection failed.")
        except Exception as error:
            self._set_disconnected(self._format_error(error))
        return None

    def _set_connected(self) -> None:
        with self._state_lock:
            self._connected = True
            self._last_error = None

        LOGGER.info(
            "RTSP frame source에 연결했습니다.",
            extra={
                "component": "media",
                "event": "rtsp_connected",
                "camera": self._name,
            },
        )

    def _set_disconnected(self, error: str) -> None:
        with self._state_lock:
            self._latest_frame = None
            self._connected = False
            self._last_error = error

        LOGGER.warning(
            "RTSP frame source 연결 또는 read에 실패했습니다.",
            extra={
                "component": "media",
                "event": "rtsp_disconnected",
                "camera": self._name,
            },
        )

    @staticmethod
    def _format_error(error: BaseException) -> str:
        detail = str(error).strip()
        return detail or type(error).__name__
