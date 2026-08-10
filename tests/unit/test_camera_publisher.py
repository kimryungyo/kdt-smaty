"""CameraPublisher의 FFmpeg process 경계 테스트."""

from __future__ import annotations

import subprocess

import pytest

from smart_desk.modules.media.publisher import CameraPublisher


class FakeProcess:
    """Popen을 실행하지 않고 종료 동작을 기록한다."""

    def __init__(self, *, exit_code: int | None = None, wait_times_out: bool = False) -> None:
        self.exit_code = exit_code
        self.wait_times_out = wait_times_out
        self.terminate_count = 0
        self.kill_count = 0
        self.wait_count = 0

    def poll(self) -> int | None:
        return self.exit_code

    def terminate(self) -> None:
        self.terminate_count += 1

    def kill(self) -> None:
        self.kill_count += 1
        self.exit_code = -9

    def wait(self, timeout: float | None = None) -> int:
        self.wait_count += 1
        if self.wait_times_out and self.kill_count == 0:
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        self.exit_code = 0 if self.exit_code is None else self.exit_code
        return self.exit_code


def make_publisher(device: str) -> CameraPublisher:
    return CameraPublisher(
        name="user",
        device=device,
        rtsp_url="rtsp://127.0.0.1:8554/user-cam",
        ffmpeg_path="ffmpeg",
        input_format="mjpeg",
        width=1280,
        height=720,
        fps=15,
    )


async def test_start_builds_safe_ffmpeg_command(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = FakeProcess()
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        calls.append((command, kwargs))
        return process

    monkeypatch.setattr("smart_desk.modules.media.publisher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("smart_desk.modules.media.publisher.INITIAL_PROCESS_CHECK_SECONDS", 0)
    publisher = make_publisher(str(tmp_path / "camera"))
    (tmp_path / "camera").touch()

    await publisher.start()
    await publisher.start()

    assert calls == [
        (
            [
                "ffmpeg", "-hide_banner", "-nostdin", "-f", "v4l2", "-input_format",
                "mjpeg", "-video_size", "1280x720", "-framerate", "15", "-i",
                str(tmp_path / "camera"), "-an", "-c:v", "libx264", "-preset",
                "ultrafast", "-tune", "zerolatency", "-pix_fmt", "yuv420p", "-bf",
                "0", "-f", "rtsp", "-rtsp_transport", "tcp",
                "rtsp://127.0.0.1:8554/user-cam",
            ],
            {"shell": False, "stdin": subprocess.DEVNULL},
        )
    ]
    assert publisher.is_running() is True


async def test_start_rejects_missing_device(tmp_path) -> None:
    publisher = make_publisher(str(tmp_path / "missing"))

    with pytest.raises(RuntimeError, match="장치를 찾을 수 없습니다"):
        await publisher.start()


async def test_start_reports_popen_and_immediate_process_failures(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = tmp_path / "camera"
    device.touch()
    publisher = make_publisher(str(device))
    monkeypatch.setattr("smart_desk.modules.media.publisher.INITIAL_PROCESS_CHECK_SECONDS", 0)

    def failing_popen(*_args: object, **_kwargs: object) -> FakeProcess:
        raise FileNotFoundError("ffmpeg missing")

    monkeypatch.setattr("smart_desk.modules.media.publisher.subprocess.Popen", failing_popen)
    with pytest.raises(RuntimeError, match="ffmpeg missing"):
        await publisher.start()

    monkeypatch.setattr(
        "smart_desk.modules.media.publisher.subprocess.Popen",
        lambda *_args, **_kwargs: FakeProcess(exit_code=1),
    )
    with pytest.raises(RuntimeError, match="exit code: 1"):
        await publisher.start()
    assert publisher.is_running() is False


async def test_stop_terminates_and_kills_after_timeout(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = tmp_path / "camera"
    device.touch()
    process = FakeProcess(wait_times_out=True)
    monkeypatch.setattr(
        "smart_desk.modules.media.publisher.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr("smart_desk.modules.media.publisher.INITIAL_PROCESS_CHECK_SECONDS", 0)
    publisher = make_publisher(str(device))

    await publisher.start()
    await publisher.stop()
    await publisher.stop()

    assert process.terminate_count == 1
    assert process.kill_count == 1
    assert process.wait_count == 2
    assert publisher.is_running() is False
