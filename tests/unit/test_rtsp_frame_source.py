"""RtspFrameSource의 thread와 최신 frame 상태 테스트."""

from __future__ import annotations

import asyncio
import logging
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


class OneFrameThenFailCapture:
    """한 frame을 성공적으로 읽은 뒤 연결이 끊기는 VideoCapture 대역이다."""

    def __init__(self) -> None:
        self._reads = 0

    def isOpened(self) -> bool:
        return True

    def read(self) -> tuple[bool, np.ndarray | None]:
        self._reads += 1
        if self._reads == 1:
            return True, np.zeros((1, 1), dtype=np.uint8)
        return False, None

    def release(self) -> None:
        return None


class OpenedNoFrameCapture:
    def isOpened(self) -> bool:
        return True

    def read(self) -> tuple[bool, None]:
        return False, None

    def release(self) -> None:
        return None


async def test_reconnect_backoff_doubles_and_resets_after_a_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captures = iter([ClosedCapture(), ClosedCapture(), OneFrameThenFailCapture()])
    monkeypatch.setattr(
        "smart_desk.modules.media.frame_source.cv2.VideoCapture",
        lambda *_args: next(captures),
    )
    source = RtspFrameSource(
        name="posture",
        rtsp_url="rtsp://media/posture",
        reconnect_interval_seconds=0.25,
    )
    retry_delays: list[float] = []

    def fake_wait(_stop_event: threading.Event, failures: int) -> bool:
        retry_delays.append(source._retry_delay(failures))
        return len(retry_delays) == 3

    monkeypatch.setattr(source, "_wait_for_retry", fake_wait)

    await source.start()
    await wait_until(lambda: len(retry_delays) == 3)
    await source.stop()

    assert retry_delays == [0.25, 0.5, 0.25]
    assert source._retry_delay(100) == 30.0
    assert source._retry_delay(10**9) == 30.0
    assert source.is_connected() is False
    assert source.get_latest_frame() is None


def test_disconnection_logging_is_transition_based_and_rate_limited(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    clock = iter([100.0, 105.0, 130.0, 131.0])
    monkeypatch.setattr(
        "smart_desk.modules.media.frame_source.time.monotonic", lambda: next(clock)
    )
    caplog.set_level(logging.WARNING, logger="smart_desk.modules.media.frame_source")
    source = RtspFrameSource(name="posture", rtsp_url="rtsp://media/posture")

    source._set_disconnected("first failure")
    source._set_disconnected("second failure")
    source._set_disconnected("third failure")
    source._set_connected()
    source._set_disconnected("read failure")

    warnings = [
        record for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 3
    assert source.get_last_error() == "read failure"
    assert source.is_connected() is False


async def test_open_without_a_frame_does_not_claim_connected_or_bypass_log_limit(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        "smart_desk.modules.media.frame_source.cv2.VideoCapture",
        lambda *_args: OpenedNoFrameCapture(),
    )
    caplog.set_level(logging.INFO, logger="smart_desk.modules.media.frame_source")
    source = RtspFrameSource(
        name="posture",
        rtsp_url="rtsp://media/posture",
        reconnect_interval_seconds=0.01,
    )

    await source.start()
    await wait_until(lambda: source.get_last_error() is not None)
    await asyncio.sleep(0.04)
    await source.stop()

    events = [getattr(record, "event", None) for record in caplog.records]
    assert "rtsp_connected" not in events
    assert events.count("rtsp_disconnected") == 1


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
    started_stop_at = time.monotonic()
    await source.stop()

    assert time.monotonic() - started_stop_at < 0.5
    assert captures[0].released is True
    assert source.is_connected() is False
    assert source.get_latest_frame() is None
