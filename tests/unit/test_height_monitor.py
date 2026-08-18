"""DeskHeightMonitor의 높이, 신선도와 MQTT 발행 계약 테스트."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from typing import Any

import pytest

from smart_desk.config.settings import DeskSettings
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.desk.height_monitor import DeskHeightMonitor
from smart_desk.modules.desk.height_cache import HeightCacheRepository
from smart_desk.modules.desk.models import HeightProvenance, HeightStatus
from smart_desk.modules.desk.segment import MASK_TO_DIGIT, SegmentDecoder
from smart_desk.modules.mqtt.client import MqttUnavailableError
from smart_desk.modules.mqtt.topics import HEIGHT_TOPIC
from smart_desk.modules.serial.source import SerialSnapshot, SerialStatus
from smart_desk.storage import SQLiteDatabase


DIGIT_TO_MASK = {digit: mask for mask, digit in MASK_TO_DIGIT.items()}


class FakeNow:
    """테스트가 직접 이동시키는 UTC 시각."""

    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FakeSerialSource:
    """queue의 line과 제어 가능한 source snapshot을 제공한다."""

    def __init__(self) -> None:
        self.lines: asyncio.Queue[bytes] = asyncio.Queue()
        self.status = SerialStatus.STOPPED
        self.last_error: str | None = None
        self.start_count = 0
        self.stop_count = 0
        self.read_count = 0

    async def start(self) -> None:
        self.start_count += 1
        self.status = SerialStatus.CONNECTED
        self.last_error = None

    async def stop(self) -> None:
        self.stop_count += 1
        self.status = SerialStatus.STOPPED

    async def read_line(self, timeout_seconds: float | None = None) -> bytes:
        del timeout_seconds
        result = await self.lines.get()
        self.read_count += 1
        return result

    def get_snapshot(self) -> SerialSnapshot:
        return SerialSnapshot(status=self.status, last_error=self.last_error)

    def put(self, line: bytes) -> None:
        self.lines.put_nowait(line)


class FakeMqttClient:
    """높이 publish 호출을 기록하고 선택적으로 실패시킨다."""

    def __init__(self, *, failures: int = 0) -> None:
        self.failures = failures
        self.publications: list[dict[str, Any]] = []
        self.attempt_count = 0

    async def publish(
        self,
        topic: str,
        payload: bytes | str,
        *,
        qos: int,
        retain: bool,
    ) -> None:
        self.attempt_count += 1
        if self.failures > 0:
            self.failures -= 1
            raise MqttUnavailableError("test unavailable")
        self.publications.append(
            {"topic": topic, "payload": payload, "qos": qos, "retain": retain}
        )


class FailingTaskManager:
    """runner 생성 실패 시 source rollback을 검증한다."""

    def create(self, _name: str, coroutine, *, critical: bool = False):
        del critical
        coroutine.close()
        raise RuntimeError("task create failed")


def height_line(digits: str, *, point_after: int | None = None, fresh: int = 7) -> bytes:
    packet: dict[str, object] = {"fresh": fresh}
    for index, number in enumerate((8, 9, 10)):
        packet[f"m{number}"] = DIGIT_TO_MASK[digits[index]]
        packet[f"p{number}"] = int(point_after == number)
    return json.dumps(packet).encode()


async def wait_until(predicate, *, timeout: float = 0.5) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


def make_monitor(
    source: FakeSerialSource,
    mqtt: FakeMqttClient,
    task_manager: TaskManager,
    now: FakeNow,
) -> DeskHeightMonitor:
    settings = DeskSettings(height_stale_after_seconds=1.0)
    return DeskHeightMonitor(
        source,  # type: ignore[arg-type]
        SegmentDecoder(settings),
        mqtt,  # type: ignore[arg-type]
        settings,
        task_manager,
        now=now,
    )


async def test_valid_height_updates_snapshot_and_publishes_retained_message() -> None:
    observed_at = datetime(2026, 8, 6, 5, 0, tzinfo=UTC)
    now = FakeNow(observed_at)
    source = FakeSerialSource()
    mqtt = FakeMqttClient()
    task_manager = TaskManager()
    monitor = make_monitor(source, mqtt, task_manager, now)

    assert monitor.get_snapshot().status is HeightStatus.STOPPED
    await monitor.start()
    assert source.start_count == 1
    assert monitor.get_snapshot().status is HeightStatus.WAITING

    source.put(height_line("802", point_after=9))
    await wait_until(lambda: len(mqtt.publications) == 1)

    snapshot = monitor.get_snapshot()
    assert snapshot.height_cm == 80.2
    assert snapshot.observed_at is observed_at
    assert snapshot.status is HeightStatus.ONLINE
    publication = mqtt.publications[0]
    assert publication["topic"] == HEIGHT_TOPIC
    assert publication["qos"] == 0
    assert publication["retain"] is True
    payload = json.loads(str(publication["payload"]))
    assert payload == {
        "schema": "smartdesk.height.v1",
        "observed_at": "2026-08-06T05:00:00Z",
        "height_cm": 80.2,
    }

    await monitor.stop()
    await task_manager.shutdown()


async def test_invalid_and_empty_frames_do_not_change_observation() -> None:
    first_time = datetime(2026, 8, 6, 5, 0, tzinfo=UTC)
    now = FakeNow(first_time)
    source = FakeSerialSource()
    mqtt = FakeMqttClient()
    task_manager = TaskManager()
    monitor = make_monitor(source, mqtt, task_manager, now)
    await monitor.start()
    source.put(height_line("750", point_after=9))
    await wait_until(lambda: len(mqtt.publications) == 1)

    now.value += timedelta(milliseconds=500)
    source.put(b"")
    source.put(b'{"status":"reader_started"}')
    source.put(height_line("802", point_after=9, fresh=0))
    await wait_until(lambda: source.read_count == 4)

    snapshot = monitor.get_snapshot()
    assert snapshot.height_cm == 75.0
    assert snapshot.observed_at is first_time
    assert len(mqtt.publications) == 1

    await monitor.stop()
    await task_manager.shutdown()


async def test_staleness_boundary_and_source_error_precedence() -> None:
    observed_at = datetime(2026, 8, 6, 5, 0, tzinfo=UTC)
    now = FakeNow(observed_at)
    source = FakeSerialSource()
    mqtt = FakeMqttClient()
    task_manager = TaskManager()
    monitor = make_monitor(source, mqtt, task_manager, now)
    await monitor.start()
    source.put(height_line("800", point_after=9))
    await wait_until(lambda: len(mqtt.publications) == 1)

    now.value = observed_at + timedelta(seconds=1)
    assert monitor.get_snapshot().status is HeightStatus.ONLINE
    now.value += timedelta(microseconds=1)
    assert monitor.get_snapshot().status is HeightStatus.STALE

    now.value = observed_at + timedelta(milliseconds=100)
    source.status = SerialStatus.ERROR
    source.last_error = "disconnected"
    assert monitor.get_snapshot().status is HeightStatus.ERROR

    await monitor.stop()
    await task_manager.shutdown()


async def test_publish_failure_keeps_observation_and_runner_continues() -> None:
    now = FakeNow(datetime(2026, 8, 6, 5, 0, tzinfo=UTC))
    source = FakeSerialSource()
    mqtt = FakeMqttClient(failures=1)
    task_manager = TaskManager()
    monitor = make_monitor(source, mqtt, task_manager, now)
    await monitor.start()

    source.put(height_line("800", point_after=9))
    await wait_until(lambda: mqtt.attempt_count == 1)
    assert monitor.get_snapshot().height_cm == 80.0
    assert mqtt.publications == []

    now.value += timedelta(milliseconds=100)
    source.put(height_line("810", point_after=9))
    await wait_until(lambda: len(mqtt.publications) == 1)
    assert monitor.get_snapshot().height_cm == 81.0
    assert mqtt.attempt_count == 2

    await monitor.stop()
    await task_manager.shutdown()


async def test_stop_is_repeatable_and_restart_clears_previous_height() -> None:
    now = FakeNow(datetime(2026, 8, 6, 5, 0, tzinfo=UTC))
    source = FakeSerialSource()
    mqtt = FakeMqttClient()
    task_manager = TaskManager()
    monitor = make_monitor(source, mqtt, task_manager, now)
    await monitor.start()
    source.put(height_line("800", point_after=9))
    await wait_until(lambda: len(mqtt.publications) == 1)

    await monitor.stop()
    await monitor.stop()
    stopped = monitor.get_snapshot()
    assert stopped.status is HeightStatus.STOPPED
    assert stopped.height_cm == 80.0
    assert source.stop_count == 1

    await monitor.start()
    restarted = monitor.get_snapshot()
    assert restarted.status is HeightStatus.WAITING
    assert restarted.height_cm is None
    assert restarted.observed_at is None

    await monitor.stop()
    assert source.stop_count == 2
    await task_manager.shutdown()


async def test_task_creation_failure_rolls_back_started_source() -> None:
    source = FakeSerialSource()
    settings = DeskSettings()
    monitor = DeskHeightMonitor(
        source,  # type: ignore[arg-type]
        SegmentDecoder(settings),
        FakeMqttClient(),  # type: ignore[arg-type]
        settings,
        FailingTaskManager(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="task create failed"):
        await monitor.start()

    assert source.start_count == 1
    assert source.stop_count == 1
    assert monitor.get_snapshot().status is HeightStatus.STOPPED


async def test_online_observation_persists_as_cached_sensor_sleeping_after_restart(
    tmp_path,
) -> None:
    observed_at = datetime(2026, 8, 6, 5, 0, tzinfo=UTC)
    now = FakeNow(observed_at)
    database = SQLiteDatabase(tmp_path / "desk.db")
    await database.start()
    cache = HeightCacheRepository(database)
    source = FakeSerialSource()
    mqtt = FakeMqttClient()
    tasks = TaskManager()
    monitor = DeskHeightMonitor(
        source,  # type: ignore[arg-type]
        SegmentDecoder(DeskSettings(height_stale_after_seconds=1.0)),
        mqtt,  # type: ignore[arg-type]
        DeskSettings(height_stale_after_seconds=1.0),
        tasks,
        now=now,
        cache=cache,
    )
    await monitor.start()
    source.put(height_line("802", point_after=9))
    await wait_until(lambda: len(mqtt.publications) == 1)
    await monitor.stop()
    await tasks.shutdown()

    restarted_source = FakeSerialSource()
    restarted_tasks = TaskManager()
    restarted = DeskHeightMonitor(
        restarted_source,  # type: ignore[arg-type]
        SegmentDecoder(DeskSettings(height_stale_after_seconds=1.0)),
        FakeMqttClient(),  # type: ignore[arg-type]
        DeskSettings(height_stale_after_seconds=1.0),
        restarted_tasks,
        now=now,
        cache=cache,
    )
    await restarted.start()

    snapshot = restarted.get_snapshot()
    assert snapshot.height_cm == 80.2
    assert snapshot.observed_at == observed_at
    assert snapshot.provenance is HeightProvenance.CACHED
    assert snapshot.status is HeightStatus.SENSOR_SLEEPING

    await restarted.stop()
    await restarted_tasks.shutdown()
    await database.stop()


async def test_naive_clock_fails_critical_runner() -> None:
    now = FakeNow(datetime(2026, 8, 6, 5, 0))
    source = FakeSerialSource()
    mqtt = FakeMqttClient()
    task_manager = TaskManager()
    monitor = make_monitor(source, mqtt, task_manager, now)
    await monitor.start()
    source.put(height_line("800", point_after=9))
    await wait_until(lambda: bool(task_manager.failures()))

    failure = task_manager.failures()[0]
    assert failure.critical is True
    assert isinstance(failure.error, ValueError)

    await monitor.stop()
    await task_manager.shutdown()


async def test_reset_display_is_held_until_a_normal_numeric_height_arrives() -> None:
    now = FakeNow(datetime(2026, 8, 6, 5, 0, tzinfo=UTC))
    source, mqtt, tasks = FakeSerialSource(), FakeMqttClient(), TaskManager()
    monitor = make_monitor(source, mqtt, tasks, now)
    await monitor.start()
    source.put(b'{"m8":5,"p8":0,"m9":83,"p9":0,"m10":15,"p10":0,"fresh":7}')
    await wait_until(monitor.panel_reset_active)
    assert mqtt.publications == []

    source.put(height_line("730", point_after=9))
    await wait_until(lambda: len(mqtt.publications) == 1)
    assert monitor.panel_reset_active() is False
    assert monitor.get_snapshot().height_cm == 73.0
    await monitor.stop()
    await tasks.shutdown()
