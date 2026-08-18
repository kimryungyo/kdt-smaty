"""TiltController의 MQTT 명령 처리와 시리얼 상태 반영 계약 테스트."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

import pytest

from smart_desk.config.settings import TiltSettings
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.mqtt.models import MqttMessage
from smart_desk.modules.mqtt.topics import TILT_STATUS_TOPIC
from smart_desk.modules.tilt.controller import TiltController
from smart_desk.modules.tilt.level_repository import TiltLevelRepository
from smart_desk.modules.tilt.models import TiltState


LEVELS = {"0": 0.0, "1": 38.0}
CALIBRATION = {
    "100": {
        "UP": [{"speed_mm_s": 15.0, "at": 1.0}],
        "DOWN": [{"speed_mm_s": 19.0, "at": 1.0}],
    }
}


class FakeMqttClient:
    """publish 호출을 기록한다."""

    def __init__(self) -> None:
        self.publications: list[dict[str, Any]] = []

    async def publish(
        self,
        topic: str,
        payload: bytes | str,
        *,
        qos: int,
        retain: bool,
    ) -> None:
        self.publications.append(
            {"topic": topic, "payload": payload, "qos": qos, "retain": retain}
        )

    def last_status(self) -> dict[str, Any]:
        for entry in reversed(self.publications):
            if entry["topic"] == TILT_STATUS_TOPIC:
                return json.loads(entry["payload"])
        raise AssertionError("틸팅 상태가 발행되지 않았습니다.")


class FakeTiltLink:
    """실제 시리얼 없이 write_line/read_line 계약을 흉내낸다."""

    def __init__(self) -> None:
        self.written: list[str] = []
        self.connection_generation = 1
        self.write_should_fail = False
        self._lines: asyncio.Queue[bytes] = asyncio.Queue()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def write_line(self, command: str) -> bool:
        self.written.append(command)
        return not self.write_should_fail

    async def read_line(self, timeout_seconds: float | None = None) -> bytes:
        try:
            return await asyncio.wait_for(self._lines.get(), timeout=0.05)
        except TimeoutError:
            return b""

    def push_device_line(self, payload: dict[str, Any]) -> None:
        self._lines.put_nowait((json.dumps(payload) + "\n").encode())


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _build(
    tmp_path: Path, *, max_level: int = 1
) -> tuple[TiltController, FakeMqttClient, FakeTiltLink]:
    levels_file = tmp_path / "levels.json"
    calibration_file = tmp_path / "calibration.json"
    _write_json(levels_file, LEVELS)
    _write_json(calibration_file, CALIBRATION)

    levels = TiltLevelRepository(levels_file, calibration_file)
    link = FakeTiltLink()
    mqtt = FakeMqttClient()
    settings = TiltSettings(
        levels_file=levels_file,
        calibration_file=calibration_file,
        min_level=0,
        max_level=max_level,
    )
    controller = TiltController(link, levels, mqtt, settings, TaskManager())  # type: ignore[arg-type]
    return controller, mqtt, link


def _goto_message(level: int) -> MqttMessage:
    payload = json.dumps({"command": "GOTO", "level": level}).encode()
    return MqttMessage(
        topic="/smartdesk/tilt/command",
        payload=payload,
        qos=1,
        retained=False,
        received_at=datetime(2026, 8, 18, tzinfo=UTC),
    )


def _stop_message() -> MqttMessage:
    payload = json.dumps({"command": "STOP"}).encode()
    return MqttMessage(
        topic="/smartdesk/tilt/command",
        payload=payload,
        qos=1,
        retained=False,
        received_at=datetime(2026, 8, 18, tzinfo=UTC),
    )


async def test_goto_level_syncs_calibration_and_sends_move_to(tmp_path: Path) -> None:
    controller, mqtt, link = _build(tmp_path)
    await controller.start()
    try:
        await controller.handle_command(_goto_message(1))

        assert "CALIBRATE 100 15.0000 UP" in link.written
        assert "CALIBRATE 100 19.0000 DOWN" in link.written
        assert link.written[-1] == "MOVE_TO 38.00 100"
        status = mqtt.last_status()
        assert status["state"] == "MOVING"
        assert status["level"] == 1
    finally:
        await controller.stop()


async def test_goto_level_without_target_is_rejected_without_serial_write(
    tmp_path: Path,
) -> None:
    # 단계 범위(0~2) 안이지만 LEVELS에는 2단계 목표가 없다.
    controller, mqtt, link = _build(tmp_path, max_level=2)
    await controller.start()
    try:
        await controller.handle_command(_goto_message(2))

        assert link.written == []
        status = mqtt.last_status()
        assert status["state"] == "ERROR"
        assert "목표 위치가 설정되지 않았습니다" in status["detail"]
    finally:
        await controller.stop()


async def test_goto_out_of_range_level_is_rejected(tmp_path: Path) -> None:
    controller, mqtt, link = _build(tmp_path)
    await controller.start()
    try:
        await controller.handle_command(_goto_message(5))

        assert link.written == []
        status = mqtt.last_status()
        assert status["state"] == "ERROR"
        assert "단계는" in status["detail"]
    finally:
        await controller.stop()


async def test_goto_while_moving_rejects_duplicate_command(tmp_path: Path) -> None:
    controller, mqtt, link = _build(tmp_path)
    await controller.start()
    try:
        await controller.handle_command(_goto_message(1))
        written_after_first = list(link.written)

        await controller.handle_command(_goto_message(0))

        assert link.written == written_after_first
        status = mqtt.last_status()
        assert status["state"] == "ERROR"
        assert "진행 중" in status["detail"]
    finally:
        await controller.stop()


async def test_device_stopped_event_returns_to_idle(tmp_path: Path) -> None:
    controller, mqtt, link = _build(tmp_path)
    await controller.start()
    try:
        await controller.handle_command(_goto_message(1))
        link.push_device_line(
            {"event": "stopped", "state": "STOP", "reason": "timeout", "position_mm": 38.0}
        )
        for _ in range(20):
            await asyncio.sleep(0.02)
            if controller.get_snapshot().state is TiltState.IDLE:
                break

        snapshot = controller.get_snapshot()
        assert snapshot.state is TiltState.IDLE
        assert snapshot.position_mm == 38.0
    finally:
        await controller.stop()


async def test_device_rejected_event_sets_error(tmp_path: Path) -> None:
    controller, mqtt, link = _build(tmp_path)
    await controller.start()
    try:
        await controller.handle_command(_goto_message(1))
        link.push_device_line({"event": "rejected", "reason": "not_calibrated"})
        for _ in range(20):
            await asyncio.sleep(0.02)
            if controller.get_snapshot().state is TiltState.ERROR:
                break

        snapshot = controller.get_snapshot()
        assert snapshot.state is TiltState.ERROR
        assert snapshot.last_error == "not_calibrated"
    finally:
        await controller.stop()


async def test_stop_command_sends_stop_and_sets_idle(tmp_path: Path) -> None:
    controller, mqtt, link = _build(tmp_path)
    await controller.start()
    try:
        await controller.handle_command(_goto_message(1))
        await controller.handle_command(_stop_message())

        assert link.written[-1] == "STOP"
        status = mqtt.last_status()
        assert status["state"] == "IDLE"
    finally:
        await controller.stop()


async def test_invalid_command_payload_is_rejected(tmp_path: Path) -> None:
    controller, mqtt, link = _build(tmp_path)
    await controller.start()
    try:
        message = MqttMessage(
            topic="/smartdesk/tilt/command",
            payload=b"{",
            qos=1,
            retained=False,
            received_at=datetime(2026, 8, 18, tzinfo=UTC),
        )
        await controller.handle_command(message)

        assert link.written == []
        status = mqtt.last_status()
        assert status["state"] == "ERROR"
    finally:
        await controller.stop()
