"""RtspFrameSource의 thread와 최신 frame 상태 테스트."""

from __future__ import annotations

import asyncio
import threading
import time

import numpy as np
import pytest

from smart_desk.modules.media.frame_source import RtspFrameSource


async def wait_until(predicate, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.001)


class RepeatingCapture:
    """마지막 frame을 계속 반환하는 VideoCapture 대역이다."""

    def __init__(self, frames: list[np.ndarray]) -> None:
        self._frames = iter(frames)
        self._last_frame: np.ndarray | None = None
        self.released = False

    def isOpened(self) -> bool:
        return True

    def read(self) -> tuple[bool, np.ndarray | None]:
        try:
            self._last_frame = next(self._frames)
        except StopIteration:
            time.sleep(0.001)
        return True, self._last_frame

    def release(self) -> None:
        self.released = True


async def test_source_keeps_only_the_latest_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    first = np.zeros((1, 1), dtype=np.uint8)
    latest = np.ones((1, 1), dtype=np.uint8)
    capture = RepeatingCapture([first, latest])
    monkeypatch.setattr(
        "smart_desk.modules.media.frame_source.cv2.VideoCapture",
        lambda *_args: capture,
    )
    source = RtspFrameSource(name="user", rtsp_url="rtsp://media/user")

    await source.start()
    await wait_until(lambda: source.get_latest_frame() is not None)
    frame, captured_at = source.get_latest_frame() or (None, None)

    assert np.array_equal(frame, latest)
    assert isinstance(captured_at, float)
    assert source.is_connected() is True
    assert source.get_last_error() is None

    await source.stop()
    assert capture.released is True
    assert source.get_latest_frame() is None


class FailingThenWorkingCapture:
    """기존 frame 뒤 read 실패를 재현한다."""

    def __init__(self, failure_gate: threading.Event, fail_event: threading.Event) -> None:
        self._failure_gate = failure_gate
        self._fail_event = fail_event
        self._reads = 0

    def isOpened(self) -> bool:
        return True

    def read(self) -> tuple[bool, np.ndarray | None]:
        self._reads += 1
        if self._reads == 1:
            return True, np.zeros((1, 1), dtype=np.uint8)
        self._failure_gate.wait(1)
        self._fail_event.set()
        return False, None

    def release(self) -> None:
        return None


async def test_read_failure_clears_frame_before_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure_gate = threading.Event()
    fail_event = threading.Event()
    failed_capture = FailingThenWorkingCapture(failure_gate, fail_event)
    recovered_frame = np.full((1, 1), 2, dtype=np.uint8)
    recovered_capture = RepeatingCapture([recovered_frame])
    captures = iter([failed_capture, recovered_capture])
    monkeypatch.setattr(
        "smart_desk.modules.media.frame_source.cv2.VideoCapture",
        lambda *_args: next(captures),
    )
    source = RtspFrameSource(
        name="user",
        rtsp_url="rtsp://media/user",
        reconnect_interval_seconds=0.2,
    )

    await source.start()
    await wait_until(lambda: source.get_latest_frame() is not None)
    failure_gate.set()
    await wait_until(fail_event.is_set)
    await wait_until(lambda: source.get_latest_frame() is None)

    assert source.is_connected() is False
    assert source.get_last_error() == "RTSP stream read failed."

    await wait_until(lambda: source.get_latest_frame() is not None)
    frame, _captured_at = source.get_latest_frame() or (None, None)
    assert np.array_equal(frame, recovered_frame)
    assert source.is_connected() is True

    await source.stop()


class ClosedCapture:
    """연결 자체가 실패하는 VideoCapture 대역이다."""

    def __init__(self) -> None:
        self.released = False

    def isOpened(self) -> bool:
        return False

    def release(self) -> None:
        self.released = True


async def test_stop_interrupts_reconnect_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    captures: list[ClosedCapture] = []

    def fake_capture(*_args: object) -> ClosedCapture:
        capture = ClosedCapture()
        captures.append(capture)
        return capture

    monkeypatch.setattr(
        "smart_desk.modules.media.frame_source.cv2.VideoCapture",
        fake_capture,
    )
    source = RtspFrameSource(
        name="posture",
        rtsp_url="rtsp://media/posture",
        reconnect_interval_seconds=5,
    )

    await source.start()
    await wait_until(lambda: source.get_last_error() is not None)
    await source.stop()

    assert captures[0].released is True
    assert source.is_connected() is False
    assert source.get_latest_frame() is None
