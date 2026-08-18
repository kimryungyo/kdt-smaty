"""Arduino 높이 관측의 최신값, 신선도와 MQTT 발행을 관리한다."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging
from typing import TypeAlias

from smart_desk.config.settings import DeskSettings
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.desk.messages import HeightMessage
from smart_desk.modules.desk.height_cache import HeightCacheRepository
from smart_desk.modules.desk.models import (
    HeightProvenance,
    HeightSnapshot,
    HeightStatus,
)
from smart_desk.modules.desk.segment import SegmentDecoder
from smart_desk.modules.mqtt.client import MqttClient, MqttUnavailableError
from smart_desk.modules.mqtt.topics import HEIGHT_TOPIC
from smart_desk.modules.serial.source import SerialLineSource, SerialStatus


LOGGER = logging.getLogger(__name__)
HEIGHT_MONITOR_TASK_NAME = "desk-height-monitor"
Now: TypeAlias = Callable[[], datetime]


def utc_now() -> datetime:
    """현재 timezone-aware UTC 시각을 반환한다."""

    return datetime.now(UTC)


class DeskHeightMonitor:
    """시리얼 높이의 단일 소유자로 최신 snapshot과 MQTT 관측을 제공한다."""

    def __init__(
        self,
        source: SerialLineSource,
        decoder: SegmentDecoder,
        mqtt: MqttClient,
        settings: DeskSettings,
        task_manager: TaskManager,
        *,
        now: Now = utc_now,
        cache: HeightCacheRepository | None = None,
    ) -> None:
        self._source = source
        self._decoder = decoder
        self._mqtt = mqtt
        self._settings = settings
        self._task_manager = task_manager
        self._now = now
        self._cache = cache
        self._runner_task: asyncio.Task[None] | None = None
        self._height_cm: float | None = None
        self._observed_at: datetime | None = None
        self._provenance: HeightProvenance | None = None
        self._panel_reset_active = False
        self._running = False
        self._source_started = False

    async def start(self) -> None:
        """source를 시작하고 하나의 critical 높이 수신 runner를 생성한다."""

        if self._running or (
            self._runner_task is not None and not self._runner_task.done()
        ):
            raise RuntimeError("책상 높이 monitor가 이미 실행 중입니다.")

        self._height_cm = None
        self._observed_at = None
        self._provenance = None
        self._panel_reset_active = False
        if self._cache is not None:
            try:
                cached = await self._cache.load()
                if cached is not None:
                    self._height_cm = cached.height_cm
                    self._observed_at = cached.observed_at
                    self._provenance = HeightProvenance.CACHED
            except Exception:
                # cache는 WAKE 근거일 뿐 serial monitor lifecycle을 막지 않는다.
                LOGGER.exception(
                    "높이 cache를 읽지 못해 빈 상태로 시작합니다.",
                    extra={"component": "desk_height", "event": "height_cache_load_failed"},
                )
        await self._source.start()
        self._source_started = True
        self._running = True
        try:
            self._runner_task = self._task_manager.create(
                HEIGHT_MONITOR_TASK_NAME,
                self._run(),
                critical=True,
            )
        except Exception:
            self._running = False
            self._source_started = False
            try:
                await self._source.stop()
            except Exception:
                LOGGER.exception(
                    "높이 monitor 시작 rollback 중 시리얼 종료에 실패했습니다.",
                    extra={
                        "component": "desk_height",
                        "event": "height_start_rollback_failed",
                    },
                )
            raise

    async def stop(self) -> None:
        """runner를 먼저 취소한 뒤 소유한 시리얼 source를 종료한다."""

        self._running = False
        task = self._runner_task
        self._runner_task = None
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        if self._source_started:
            self._source_started = False
            await self._source.stop()

    def get_snapshot(self) -> HeightSnapshot:
        """현재 시각과 source 상태로 높이의 사용 가능 상태를 계산한다."""

        if not self._running:
            return HeightSnapshot(
                height_cm=self._height_cm,
                observed_at=self._observed_at,
                status=HeightStatus.STOPPED,
                provenance=self._provenance,
            )

        source_snapshot = self._source.get_snapshot()
        if source_snapshot.status is SerialStatus.ERROR:
            status = HeightStatus.ERROR
        elif self._height_cm is None or self._observed_at is None:
            status = HeightStatus.WAITING
        elif self._provenance is HeightProvenance.CACHED:
            status = HeightStatus.SENSOR_SLEEPING
        else:
            now = self._require_utc(self._now())
            age = now - self._observed_at
            if age > timedelta(seconds=self._settings.height_stale_after_seconds):
                status = HeightStatus.STALE
            elif source_snapshot.status is SerialStatus.CONNECTED:
                status = HeightStatus.ONLINE
            else:
                status = HeightStatus.ERROR

        return HeightSnapshot(
            height_cm=self._height_cm,
            observed_at=self._observed_at,
            status=status,
            provenance=self._provenance,
        )

    def panel_reset_active(self) -> bool:
        """Whether the latest complete panel frame is the non-numeric ``rSt`` reset signal."""

        return self._panel_reset_active

    async def _run(self) -> None:
        while self._running:
            raw_message = await self._source.read_line()
            if raw_message == b"":
                await asyncio.sleep(0)
                continue

            if self._decoder.is_reset_display(raw_message):
                self._panel_reset_active = True
                LOGGER.warning(
                    "책상 표시기가 rSt 초기화 상태를 보고했습니다.",
                    extra={"component": "desk_height", "event": "panel_reset_detected"},
                )
                continue

            height = self._decoder.decode(raw_message)
            if height is None:
                continue

            if self._source.get_snapshot().status is not SerialStatus.CONNECTED:
                # 끊어진 source가 남긴 line은 ONLINE 관측이나 영속 cache가 될 수 없다.
                continue

            observed_at = self._require_utc(self._now())
            self._height_cm = height
            self._panel_reset_active = False
            self._observed_at = observed_at
            self._provenance = HeightProvenance.LIVE
            LOGGER.debug(
                "유효한 책상 높이를 관측했습니다.",
                extra={"component": "desk_height", "event": "height_observed"},
            )
            await self._persist_height(height, observed_at)
            await self._publish_height(height, observed_at)

    async def _persist_height(self, height: float, observed_at: datetime) -> None:
        """ONLINE으로 검증된 관측만 best-effort로 영속화한다."""

        if self._cache is None:
            return
        try:
            await self._cache.save(height, observed_at)
        except Exception:
            # 관측과 MQTT 발행은 유지한다. cache 장애가 serial runner를 죽이지 않는다.
            LOGGER.exception(
                "유효한 책상 높이를 SQLite cache에 저장하지 못했습니다.",
                extra={"component": "desk_height", "event": "height_cache_save_failed"},
            )

    async def _publish_height(self, height: float, observed_at: datetime) -> None:
        message = HeightMessage(observed_at=observed_at, height_cm=height)
        try:
            await self._mqtt.publish(
                HEIGHT_TOPIC,
                message.model_dump_json(),
                # Height is a real-time control input.  A delayed value after an
                # ESP32 reconnect is worse than a dropped one.
                qos=0,
                retain=True,
            )
        except MqttUnavailableError:
            LOGGER.warning(
                "책상 높이를 MQTT로 발행하지 못했습니다.",
                extra={
                    "component": "desk_height",
                    "event": "height_publish_failed",
                },
                exc_info=True,
            )

    @staticmethod
    def _require_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("높이 monitor 시각은 timezone-aware UTC여야 합니다.")
        return value
