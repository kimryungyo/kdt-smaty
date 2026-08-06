"""SerialLineSource의 lazy 연결, 재연결과 종료 계약 테스트."""

from __future__ import annotations

import asyncio
from collections import deque
from threading import Event
from typing import Any

import pytest
import serial

from smart_desk.config.settings import SerialSettings
from smart_desk.modules.serial.source import (
    SerialLineSource,
    SerialStatus,
)


class FakeSerialConnection:
    """테스트가 준비한 read 결과와 close 기록을 제공한다."""

    def __init__(self, *reads: object, timeout: float = 0.2) -> None:
        self.timeout = timeout
        self.reads = deque(reads)
        self.read_timeouts: list[float] = []
        self.close_count = 0

    def readline(self) -> bytes:
        self.read_timeouts.append(self.timeout)
        result = self.reads.popleft() if self.reads else b""
        if isinstance(result, BaseException):
            raise result
        if not isinstance(result, bytes):
            raise AssertionError("fake read 결과는 bytes 또는 예외여야 합니다.")
        return result

    def close(self) -> None:
        self.close_count += 1


class BlockingSerialConnection(FakeSerialConnection):
    """worker thread read가 끝나기 전 취소되는 상황을 재현한다."""

    def __init__(self) -> None:
        super().__init__()
        self.read_started = Event()
        self.release_read = Event()

    def readline(self) -> bytes:
        self.read_started.set()
        if not self.release_read.wait(timeout=1):
            raise AssertionError("테스트가 blocking read를 해제하지 않았습니다.")
        return b'{"line":1}\n'


class FakeSerialFactory:
    """open 시도마다 준비된 connection 또는 예외를 반환한다."""

    def __init__(self, *results: object) -> None:
        self.results = deque(results)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> FakeSerialConnection:
        self.calls.append(kwargs)
        if not self.results:
            raise AssertionError("준비된 fake serial open 결과가 없습니다.")
        result = self.results.popleft()
        if isinstance(result, BaseException):
            raise result
        if not isinstance(result, FakeSerialConnection):
            raise AssertionError("fake serial connection이 아닙니다.")
        result.timeout = kwargs["timeout"]
        return result


def serial_settings(**overrides: object) -> SerialSettings:
    values: dict[str, object] = {
        "port": "/dev/test-desk",
        "read_timeout_seconds": 0.01,
        "reconnect_interval_seconds": 0.001,
    }
    values.update(overrides)
    return SerialSettings(**values)


async def wait_until(predicate, *, timeout: float = 0.5) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


async def test_start_is_lazy_and_first_read_opens_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeSerialConnection(b"height\n")
    factory = FakeSerialFactory(connection)
    monkeypatch.setattr("smart_desk.modules.serial.source.serial.Serial", factory)
    source = SerialLineSource(serial_settings())

    assert source.get_snapshot().status is SerialStatus.STOPPED
    assert factory.calls == []

    await source.start()
    assert source.get_snapshot().status is SerialStatus.DISCONNECTED
    assert factory.calls == []

    assert await source.read_line() == b"height\n"
    assert factory.calls == [
        {"port": "/dev/test-desk", "baudrate": 115200, "timeout": 0.01}
    ]
    assert source.get_snapshot().status is SerialStatus.CONNECTED

    with pytest.raises(RuntimeError, match="이미 실행"):
        await source.start()

    await source.stop()


async def test_empty_read_is_normal_timeout_and_custom_timeout_is_restored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeSerialConnection(b"")
    monkeypatch.setattr(
        "smart_desk.modules.serial.source.serial.Serial",
        FakeSerialFactory(connection),
    )
    source = SerialLineSource(serial_settings())
    await source.start()

    assert await source.read_line(timeout_seconds=0.03) == b""
    assert connection.read_timeouts == [0.03]
    assert connection.timeout == 0.01
    assert source.get_snapshot().status is SerialStatus.CONNECTED

    await source.stop()


async def test_read_validates_lifecycle_and_timeout() -> None:
    source = SerialLineSource(serial_settings())

    with pytest.raises(RuntimeError, match="시작"):
        await source.read_line()

    await source.start()
    for invalid in (True, "0.1"):
        with pytest.raises(TypeError, match="숫자"):
            await source.read_line(invalid)  # type: ignore[arg-type]
    for invalid in (0, -1, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="finite 양수"):
            await source.read_line(invalid)
    await source.stop()


async def test_open_failure_records_error_and_next_call_reconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeSerialConnection(b"after-reconnect\n")
    factory = FakeSerialFactory(
        serial.SerialException("device missing"),
        connection,
    )
    monkeypatch.setattr("smart_desk.modules.serial.source.serial.Serial", factory)
    source = SerialLineSource(serial_settings())
    await source.start()

    assert await source.read_line() == b""
    snapshot = source.get_snapshot()
    assert snapshot.status is SerialStatus.ERROR
    assert snapshot.last_error == "device missing"

    assert await source.read_line() == b"after-reconnect\n"
    assert len(factory.calls) == 2
    assert source.get_snapshot().status is SerialStatus.CONNECTED
    assert source.get_snapshot().last_error is None

    await source.stop()


async def test_read_disconnect_closes_connection_and_reconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = FakeSerialConnection(serial.SerialException("disconnected"))
    second = FakeSerialConnection(b"new-device\n")
    factory = FakeSerialFactory(first, second)
    monkeypatch.setattr("smart_desk.modules.serial.source.serial.Serial", factory)
    source = SerialLineSource(serial_settings())
    await source.start()

    assert await source.read_line() == b""
    assert first.close_count == 1
    assert source.get_snapshot().status is SerialStatus.ERROR

    assert await source.read_line() == b"new-device\n"
    assert source.get_snapshot().status is SerialStatus.CONNECTED

    await source.stop()
    assert second.close_count == 1


async def test_stop_is_repeatable_and_restart_opens_a_new_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = FakeSerialConnection(b"first\n")
    second = FakeSerialConnection(b"second\n")
    factory = FakeSerialFactory(first, second)
    monkeypatch.setattr("smart_desk.modules.serial.source.serial.Serial", factory)
    source = SerialLineSource(serial_settings())

    await source.start()
    assert await source.read_line() == b"first\n"
    await source.stop()
    await source.stop()
    assert first.close_count == 1
    assert source.get_snapshot().status is SerialStatus.STOPPED

    await source.start()
    assert await source.read_line() == b"second\n"
    await source.stop()
    assert second.close_count == 1


async def test_cancelled_read_finishes_worker_before_connection_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = BlockingSerialConnection()
    monkeypatch.setattr(
        "smart_desk.modules.serial.source.serial.Serial",
        FakeSerialFactory(connection),
    )
    source = SerialLineSource(serial_settings())
    await source.start()
    read_task = asyncio.create_task(source.read_line())
    await wait_until(connection.read_started.is_set)

    read_task.cancel()
    await asyncio.sleep(0)
    assert read_task.done() is False
    assert connection.close_count == 0

    connection.release_read.set()
    await asyncio.gather(read_task, return_exceptions=True)
    await source.stop()
    assert connection.close_count == 1
