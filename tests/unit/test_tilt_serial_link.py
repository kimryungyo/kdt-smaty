"""TiltSerialLink의 종료 STOP 전용 연결 정책 테스트."""

from __future__ import annotations

from typing import Any

import pytest

from smart_desk.config.settings import TiltSettings
from smart_desk.modules.tilt.serial_link import TiltLinkStatus, TiltSerialLink


class FakeSerialConnection:
    def __init__(self) -> None:
        self.timeout = 0.2
        self.writes: list[bytes] = []
        self.flush_count = 0
        self.close_count = 0

    def write(self, payload: bytes) -> int:
        self.writes.append(payload)
        return len(payload)

    def flush(self) -> None:
        self.flush_count += 1

    def readline(self) -> bytes:
        return b""

    def close(self) -> None:
        self.close_count += 1


def settings() -> TiltSettings:
    return TiltSettings(serial_port="/dev/tilt-test", read_timeout_seconds=0.01)


async def test_shutdown_write_does_not_open_missing_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def unexpected_open(**kwargs: Any) -> FakeSerialConnection:
        calls.append(kwargs)
        raise AssertionError("종료 STOP은 새 포트를 열면 안 됩니다.")

    monkeypatch.setattr("smart_desk.modules.tilt.serial_link.serial.Serial", unexpected_open)
    link = TiltSerialLink(settings())
    await link.start()
    try:
        assert await link.write_line_if_connected("STOP") is False
        assert calls == []
    finally:
        await link.stop()


async def test_connected_shutdown_write_reuses_current_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeSerialConnection()
    monkeypatch.setattr(
        "smart_desk.modules.tilt.serial_link.serial.Serial",
        lambda **_kwargs: connection,
    )
    link = TiltSerialLink(settings())
    await link.start()
    try:
        assert await link.write_line("STATUS") is True
        assert await link.write_line_if_connected("STOP") is True
        assert connection.writes == [b"STOP\n", b"STATUS\n", b"STOP\n"]
        assert link.get_snapshot().status is TiltLinkStatus.CONNECTED
    finally:
        await link.stop()
    assert connection.close_count == 1
