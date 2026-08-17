"""물리 카메라 하나를 FFmpeg로 MediaMTX에 발행한다."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import subprocess


LOGGER = logging.getLogger(__name__)
INITIAL_PROCESS_CHECK_SECONDS = 0.1
PROCESS_STOP_TIMEOUT_SECONDS = 5.0


class CameraPublisher:
    """카메라 하나와 자신이 시작한 FFmpeg process 하나를 소유한다."""

    def __init__(
        self,
        *,
        name: str,
        device: str,
        rtsp_url: str,
        ffmpeg_path: str,
        input_format: str,
        width: int,
        height: int,
        fps: int,
    ) -> None:
        self._name = name
        self._device = device
        self._rtsp_url = rtsp_url
        self._ffmpeg_path = ffmpeg_path
        self._input_format = input_format
        self._width = width
        self._height = height
        self._fps = fps
        self._process: subprocess.Popen[bytes] | None = None

    async def start(self) -> None:
        """FFmpeg를 시작하고 즉시 실패한 process는 시작 실패로 처리한다."""

        if self.is_running():
            return
        if not Path(self._device).exists():
            raise RuntimeError(
                f"카메라 publisher '{self._name}' 장치를 찾을 수 없습니다: {self._device}"
            )

        try:
            process = subprocess.Popen(
                self._build_command(),
                shell=False,
                stdin=subprocess.DEVNULL,
            )
        except OSError as error:
            raise RuntimeError(
                f"카메라 publisher '{self._name}'의 FFmpeg를 시작하지 못했습니다: {error}"
            ) from error

        self._process = process
        await asyncio.sleep(INITIAL_PROCESS_CHECK_SECONDS)
        exit_code = process.poll()
        if exit_code is not None:
            self._process = None
            raise RuntimeError(
                f"카메라 publisher '{self._name}'의 FFmpeg가 즉시 종료되었습니다 "
                f"(exit code: {exit_code})."
            )

        LOGGER.info(
            "카메라 FFmpeg publisher를 시작했습니다.",
            extra={
                "component": "media",
                "event": "camera_publisher_started",
                "camera": self._name,
            },
        )

    async def stop(self) -> None:
        """자신이 시작한 FFmpeg만 안전하게 종료한다."""

        process = self._process
        self._process = None
        if process is None:
            return
        if process.poll() is not None:
            return

        process.terminate()
        try:
            await asyncio.to_thread(process.wait, PROCESS_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            await asyncio.to_thread(process.wait, PROCESS_STOP_TIMEOUT_SECONDS)

        LOGGER.info(
            "카메라 FFmpeg publisher를 종료했습니다.",
            extra={
                "component": "media",
                "event": "camera_publisher_stopped",
                "camera": self._name,
            },
        )

    def is_running(self) -> bool:
        """자신이 시작한 FFmpeg process가 아직 실행 중인지 반환한다."""

        return self._process is not None and self._process.poll() is None

    def _build_command(self) -> list[str]:
        return [
            self._ffmpeg_path,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "warning",
            "-nostats",
            "-f",
            "v4l2",
            "-input_format",
            self._input_format,
            "-video_size",
            f"{self._width}x{self._height}",
            "-framerate",
            str(self._fps),
            "-i",
            self._device,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-pix_fmt",
            "yuv420p",
            "-bf",
            "0",
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            self._rtsp_url,
        ]
