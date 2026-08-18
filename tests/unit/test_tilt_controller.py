"""TiltController의 준비·STOP 우선·상태전이 계약 테스트."""

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
    def __init__(self) -> None:
        self.publications: list[dict[str, Any]] = []

    async def publish(self, topic: str, payload: bytes | str, *, qos: int, retain: bool) -> None:
        self.publications.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})

    def last_status(self) -> dict[str, Any]:
        for entry in reversed(self.publications):
            if entry["topic"] == TILT_STATUS_TOPIC:
                return json.loads(entry["payload"])
        raise AssertionError("틸팅 상태가 발행되지 않았습니다.")


class FakeTiltLink:
    """ESP32 ready/calibrated 이벤트를 내보내는 시리얼 link fake다."""

    def __init__(self) -> None:
        self.written: list[str] = []
        self.connection_generation = 1
        self.fail_prefix: str | None = None
        self.block_move = False
        self.move_started = asyncio.Event()
        self.release_move = asyncio.Event()
        self.stopped = False
        self._lines: asyncio.Queue[bytes] = asyncio.Queue()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self.stopped = True

    async def write_line(self, command: str) -> bool:
        self.written.append(command)
        if self.fail_prefix is not None and command.startswith(self.fail_prefix):
            return False
        if command.startswith("CALIBRATE "):
            _, duty, _speed, direction = command.split()
            self.push({"event": "calibrated", "duty": int(duty), "direction": direction})
        if command.startswith("MOVE_TO ") and self.block_move:
            self.move_started.set()
            await self.release_move.wait()
        return True

    async def write_line_if_connected(self, command: str) -> bool:
        self.written.append(f"connected:{command}")
        return True

    async def read_line(self, timeout_seconds: float | None = None) -> bytes:
        try:
            return await asyncio.wait_for(self._lines.get(), timeout=0.02)
        except TimeoutError:
            return b""

    def push(self, payload: dict[str, Any]) -> None:
        self._lines.put_nowait((json.dumps(payload) + "\n").encode())


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def build(tmp_path: Path) -> tuple[TiltController, FakeMqttClient, FakeTiltLink]:
    levels_file = tmp_path / "levels.json"
    calibration_file = tmp_path / "calibration.json"
    write_json(levels_file, LEVELS)
    write_json(calibration_file, CALIBRATION)
    levels = TiltLevelRepository(levels_file, calibration_file)
    link = FakeTiltLink()
    mqtt = FakeMqttClient()
    settings = TiltSettings(
        levels_file=levels_file,
        calibration_file=calibration_file,
        min_level=0,
        max_level=1,
        event_timeout_seconds=0.1,
    )
    return TiltController(link, levels, mqtt, settings, TaskManager()), mqtt, link  # type: ignore[arg-type]


async def wait_until(predicate, *, timeout: float = 0.5) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


async def ready(controller: TiltController, link: FakeTiltLink) -> None:
    link.push({"event": "ready", "firmware": "tilt-test", "position_valid": True, "position_mm": 0.0})
    await wait_until(lambda: controller.get_snapshot().state is TiltState.IDLE)


def goto(level: int, *, retained: bool = False) -> MqttMessage:
    return MqttMessage(
        topic="/smartdesk/tilt/command",
        payload=json.dumps({"command": "GOTO", "level": level}).encode(),
        qos=1,
        retained=retained,
        received_at=datetime(2026, 8, 19, tzinfo=UTC),
    )


def stop(*, retained: bool = False) -> MqttMessage:
    return MqttMessage(
        topic="/smartdesk/tilt/command",
        payload=b'{"command":"STOP"}',
        qos=1,
        retained=retained,
        received_at=datetime(2026, 8, 19, tzinfo=UTC),
    )


async def test_ready_event_syncs_all_calibration_before_idle(tmp_path: Path) -> None:
    controller, mqtt, link = build(tmp_path)
    await controller.start()
    try:
        await ready(controller, link)

        assert "CALIBRATE 100 15.0000 UP" in link.written
        assert "CALIBRATE 100 19.0000 DOWN" in link.written
        assert controller.get_snapshot().position_valid is True
        assert mqtt.last_status()["state"] == "IDLE"
    finally:
        await controller.stop()


async def test_status_event_after_server_restart_syncs_calibration(tmp_path: Path) -> None:
    controller, _mqtt, link = build(tmp_path)
    await controller.start()
    try:
        link.push({"event": "status", "firmware": "tilt-test", "position_valid": True, "position_mm": 38.0})
        await wait_until(lambda: controller.get_snapshot().state is TiltState.IDLE)

        assert "CALIBRATE 100 15.0000 UP" in link.written
        assert "CALIBRATE 100 19.0000 DOWN" in link.written
    finally:
        await controller.stop()


async def test_goto_only_confirms_level_after_at_target(tmp_path: Path) -> None:
    controller, _mqtt, link = build(tmp_path)
    await controller.start()
    try:
        await ready(controller, link)
        await controller.set_target(1)
        await wait_until(lambda: "MOVE_TO 38.00 100" in link.written)

        moving = controller.get_snapshot()
        assert moving.state is TiltState.MOVING
        assert moving.level is None
        assert moving.target_level == 1

        link.push({"event": "at_target", "position_valid": True, "position_mm": 38.0})
        await wait_until(lambda: controller.get_snapshot().state is TiltState.AT_TARGET)
        reached = controller.get_snapshot()
        assert reached.level == 1
        assert reached.target_level is None
    finally:
        await controller.stop()


async def test_calibration_failure_blocks_move_to(tmp_path: Path) -> None:
    controller, _mqtt, link = build(tmp_path)
    link.fail_prefix = "CALIBRATE"
    await controller.start()
    try:
        link.push({"event": "ready", "position_valid": True, "position_mm": 0.0})
        await wait_until(lambda: controller.get_snapshot().state is TiltState.ERROR)
        await controller.handle_command(goto(1))
        await asyncio.sleep(0)

        assert not any(command.startswith("MOVE_TO") for command in link.written)
    finally:
        await controller.stop()


async def test_stop_cancels_pending_move_before_later_move_can_complete(tmp_path: Path) -> None:
    controller, _mqtt, link = build(tmp_path)
    link.block_move = True
    await controller.start()
    try:
        await ready(controller, link)
        await controller.handle_command(goto(1))
        await wait_until(link.move_started.is_set)

        await controller.handle_command(stop())

        assert "STOP" in link.written
        assert controller.get_snapshot().state is TiltState.STOPPED
        link.release_move.set()
    finally:
        await controller.stop()


async def test_timeout_is_error_not_success(tmp_path: Path) -> None:
    controller, _mqtt, link = build(tmp_path)
    await controller.start()
    try:
        await ready(controller, link)
        await controller.handle_command(goto(1))
        await wait_until(lambda: "MOVE_TO 38.00 100" in link.written)
        link.push({"event": "stopped", "reason": "motion_timeout", "position_valid": True, "position_mm": 20.0})
        await wait_until(lambda: controller.get_snapshot().state is TiltState.ERROR)

        snapshot = controller.get_snapshot()
        assert snapshot.position_valid is False
        assert snapshot.level is None
    finally:
        await controller.stop()


async def test_retained_goto_is_not_executed_and_duplicate_target_is_idempotent(tmp_path: Path) -> None:
    controller, _mqtt, link = build(tmp_path)
    await controller.start()
    try:
        await ready(controller, link)
        await controller.handle_command(goto(1, retained=True))
        assert not any(command.startswith("MOVE_TO") for command in link.written)

        await controller.handle_command(goto(1))
        await wait_until(lambda: "MOVE_TO 38.00 100" in link.written)
        writes = list(link.written)
        await controller.handle_command(goto(1))

        assert link.written == writes
        assert controller.get_snapshot().state is TiltState.MOVING
    finally:
        await controller.stop()


async def test_shutdown_sends_stop_without_opening_a_new_connection(tmp_path: Path) -> None:
    controller, _mqtt, link = build(tmp_path)
    await controller.start()
    await controller.stop()

    assert link.written == ["connected:STOP"]
    assert link.stopped is True
