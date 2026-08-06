"""Arduino 시리얼 포트를 비동기 라인 입력으로 감싼다."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
import logging
import math
from typing import TypeVar

import serial

from smart_desk.config.settings import SerialSettings


LOGGER = logging.getLogger(__name__)
EXPECTED_SERIAL_ERRORS = (serial.SerialException, OSError)
ResultT = TypeVar("ResultT")


class SerialStatus(StrEnum):
    """Arduino 시리얼 연결의 현재 상태."""

    STOPPED = "STOPPED"
    DISCONNECTED = "DISCONNECTED"
    CONNECTED = "CONNECTED"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class SerialSnapshot:
    """외부에 제공하는 불변 시리얼 상태."""

    status: SerialStatus
    last_error: str | None


class SerialLineSource:
    """시리얼 포트 하나를 소유하며 완성된 bytes 라인을 제공한다."""

    def __init__(self, settings: SerialSettings) -> None:
        self._settings = settings
        self._connection: serial.Serial | None = None
        self._status = SerialStatus.STOPPED
        self._last_error: str | None = None
        self._started = False
        self._io_lock = asyncio.Lock()
        self._next_connect_at = 0.0

    async def start(self) -> None:
        """포트를 열지 않고 lazy 연결이 가능한 상태로 전환한다."""

        async with self._io_lock:
            if self._started:
                raise RuntimeError("시리얼 source가 이미 실행 중입니다.")
            self._started = True
            self._connection = None
            self._status = SerialStatus.DISCONNECTED
            self._last_error = None
            self._next_connect_at = 0.0

    async def stop(self) -> None:
        """진행 중인 장치 I/O 뒤 연결을 닫고 반복 호출 가능하게 종료한다."""

        self._started = False
        async with self._io_lock:
            connection = self._connection
            self._connection = None
            close_error: BaseException | None = None
            if connection is not None:
                try:
                    await self._run_blocking(connection.close)
                except EXPECTED_SERIAL_ERRORS as error:
                    close_error = error

            self._status = SerialStatus.STOPPED
            self._next_connect_at = 0.0
            if close_error is not None:
                self._last_error = self._format_error(close_error)

        if connection is not None:
            LOGGER.info(
                "Arduino 시리얼 연결을 종료했습니다.",
                extra={"component": "serial", "event": "serial_stopped"},
            )

    async def read_line(
        self,
        timeout_seconds: float | None = None,
    ) -> bytes:
        """필요하면 포트를 열고 한 줄을 읽으며 정상 timeout은 빈 bytes로 반환한다."""

        timeout = self._validate_timeout(timeout_seconds)
        if not self._started:
            raise RuntimeError("시리얼 source를 시작한 뒤 읽어야 합니다.")

        await self._wait_for_reconnect()
        async with self._io_lock:
            if not self._started:
                raise RuntimeError("종료된 시리얼 source에서는 읽을 수 없습니다.")
            if self._connection is None and not await self._open_connection():
                return b""

            connection = self._connection
            if connection is None:
                return b""
            try:
                result = await self._run_blocking(
                    lambda: self._read_with_timeout(connection, timeout)
                )
            except asyncio.CancelledError:
                raise
            except EXPECTED_SERIAL_ERRORS as error:
                await self._mark_disconnected(connection, error)
                return b""

            if not isinstance(result, bytes):
                raise TypeError("pyserial readline()은 bytes를 반환해야 합니다.")
            return result

    def get_snapshot(self) -> SerialSnapshot:
        """장치 I/O 없이 현재 연결 상태를 반환한다."""

        return SerialSnapshot(status=self._status, last_error=self._last_error)

    async def _wait_for_reconnect(self) -> None:
        remaining = self._next_connect_at - asyncio.get_running_loop().time()
        if remaining <= 0:
            return
        LOGGER.debug(
            "Arduino 시리얼 재연결 시각을 기다립니다.",
            extra={"component": "serial", "event": "serial_reconnect_wait"},
        )
        await asyncio.sleep(remaining)

    async def _open_connection(self) -> bool:
        try:
            connection = await self._open_port()
        except asyncio.CancelledError:
            raise
        except EXPECTED_SERIAL_ERRORS as error:
            self._record_error(error)
            LOGGER.warning(
                "Arduino 시리얼 포트를 열지 못했습니다.",
                extra={"component": "serial", "event": "serial_connect_failed"},
            )
            return False

        if not self._started:
            await self._close_after_cancel(connection)
            return False
        self._connection = connection
        self._status = SerialStatus.CONNECTED
        self._last_error = None
        self._next_connect_at = 0.0
        LOGGER.info(
            "Arduino 시리얼 포트에 연결했습니다.",
            extra={"component": "serial", "event": "serial_connected"},
        )
        return True

    async def _open_port(self) -> serial.Serial:
        task = asyncio.create_task(
            asyncio.to_thread(
                serial.Serial,
                port=self._settings.port,
                baudrate=self._settings.baudrate,
                timeout=self._settings.read_timeout_seconds,
            )
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            if not isinstance(result, BaseException):
                await self._close_after_cancel(result)
            raise

    def _read_with_timeout(
        self,
        connection: serial.Serial,
        timeout_seconds: float,
    ) -> bytes:
        connection.timeout = timeout_seconds
        try:
            return connection.readline()
        finally:
            connection.timeout = self._settings.read_timeout_seconds

    async def _mark_disconnected(
        self,
        connection: serial.Serial,
        error: BaseException,
    ) -> None:
        if self._connection is connection:
            self._connection = None
        try:
            await self._run_blocking(connection.close)
        except EXPECTED_SERIAL_ERRORS:
            pass
        self._record_error(error)
        LOGGER.warning(
            "Arduino 시리얼 연결이 끊어졌습니다.",
            extra={"component": "serial", "event": "serial_disconnected"},
        )

    def _record_error(self, error: BaseException) -> None:
        self._status = SerialStatus.ERROR
        self._last_error = self._format_error(error)
        self._next_connect_at = (
            asyncio.get_running_loop().time()
            + self._settings.reconnect_interval_seconds
        )

    async def _close_after_cancel(self, connection: serial.Serial) -> None:
        try:
            await self._run_blocking(connection.close)
        except EXPECTED_SERIAL_ERRORS:
            pass

    async def _run_blocking(self, operation: Callable[[], ResultT]) -> ResultT:
        task = asyncio.create_task(asyncio.to_thread(operation))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.gather(task, return_exceptions=True)
            raise

    def _validate_timeout(self, value: float | None) -> float:
        if value is None:
            return self._settings.read_timeout_seconds
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("시리얼 read timeout은 숫자여야 합니다.")
        timeout = float(value)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("시리얼 read timeout은 finite 양수여야 합니다.")
        return timeout

    @staticmethod
    def _format_error(error: BaseException) -> str:
        detail = str(error).strip()
        return detail or type(error).__name__
