"""틸팅 ESP32와의 양방향 시리얼 연결을 관리한다.

`modules/serial/source.py`의 `SerialLineSource`(Arduino 높이 리더 전용,
읽기만)와 동일한 lazy-connect·재연결·`asyncio.to_thread` 블로킹 래핑
패턴을 따르되, 틸팅 ESP32는 명령을 텍스트 줄로 받으므로 쓰기 경로가
추가로 필요하다. 연결을 새로 열 때마다 안전을 위해 `STOP`을 먼저 보내고,
이후 재연결 여부를 `connection_generation`으로 노출해 상위
`TiltController`가 보정 테이블 재동기화가 필요한 시점을 알 수 있게 한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
import logging
from typing import TypeVar

import serial

from smart_desk.config.settings import TiltSettings


LOGGER = logging.getLogger(__name__)
EXPECTED_SERIAL_ERRORS = (serial.SerialException, OSError)
ResultT = TypeVar("ResultT")


class TiltLinkStatus(StrEnum):
    """틸팅 ESP32 시리얼 연결의 현재 상태."""

    STOPPED = "STOPPED"
    DISCONNECTED = "DISCONNECTED"
    CONNECTED = "CONNECTED"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class TiltLinkSnapshot:
    """외부에 제공하는 불변 시리얼 연결 상태."""

    status: TiltLinkStatus
    last_error: str | None


class TiltSerialLink:
    """틸팅 ESP32 시리얼 포트 하나를 소유하며 명령 전송과 라인 수신을 제공한다."""

    def __init__(self, settings: TiltSettings) -> None:
        self._settings = settings
        self._connection: serial.Serial | None = None
        self._status = TiltLinkStatus.STOPPED
        self._last_error: str | None = None
        self._started = False
        self._io_lock = asyncio.Lock()
        self._next_connect_at = 0.0
        self._connection_generation = 0

    @property
    def connection_generation(self) -> int:
        """연결이 새로 열릴 때마다 증가하는 세대 번호. 재연결 감지에 쓴다."""

        return self._connection_generation

    async def start(self) -> None:
        """포트를 열지 않고 lazy 연결이 가능한 상태로 전환한다."""

        async with self._io_lock:
            if self._started:
                raise RuntimeError("틸팅 시리얼 link가 이미 실행 중입니다.")
            self._started = True
            self._connection = None
            self._status = TiltLinkStatus.DISCONNECTED
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
            self._status = TiltLinkStatus.STOPPED
            self._next_connect_at = 0.0
            if close_error is not None:
                self._last_error = self._format_error(close_error)

        if connection is not None:
            LOGGER.info(
                "틸팅 ESP32 시리얼 연결을 종료했습니다.",
                extra={"component": "tilt_serial", "event": "tilt_serial_stopped"},
            )

    async def write_line(self, command: str) -> bool:
        """필요하면 포트를 열고 명령 한 줄을 전송한다. 실패하면 False."""

        if not self._started:
            raise RuntimeError("시리얼 link를 시작한 뒤 전송해야 합니다.")

        await self._wait_for_reconnect()
        async with self._io_lock:
            if not self._started:
                raise RuntimeError("종료된 시리얼 link로는 전송할 수 없습니다.")
            if self._connection is None and not await self._open_connection():
                return False
            connection = self._connection
            if connection is None:
                return False
            try:
                await self._run_blocking(
                    lambda: connection.write((command + "\n").encode("ascii"))
                )
                await self._run_blocking(connection.flush)
            except EXPECTED_SERIAL_ERRORS as error:
                await self._mark_disconnected(connection, error)
                return False
            return True

    async def write_line_if_connected(self, command: str) -> bool:
        """이미 연결된 포트에만 명령을 쓴다.

        종료 STOP처럼 새 연결을 열거나 reconnect backoff를 기다리면 안 되는 경로에서
        사용한다. 연결이 없으면 firmware의 독립 watchdog에 맡기고 False를 반환한다.
        """

        if not self._started:
            return False
        async with self._io_lock:
            if not self._started or self._connection is None:
                return False
            connection = self._connection
            try:
                await self._run_blocking(
                    lambda: connection.write((command + "\n").encode("ascii"))
                )
                await self._run_blocking(connection.flush)
            except EXPECTED_SERIAL_ERRORS as error:
                await self._mark_disconnected(connection, error)
                return False
            return True

    async def read_line(self, timeout_seconds: float | None = None) -> bytes:
        """필요하면 포트를 열고 한 줄을 읽으며 정상 timeout은 빈 bytes로 반환한다."""

        timeout = self._validate_timeout(timeout_seconds)
        if not self._started:
            raise RuntimeError("시리얼 link를 시작한 뒤 읽어야 합니다.")

        await self._wait_for_reconnect()
        async with self._io_lock:
            if not self._started:
                raise RuntimeError("종료된 시리얼 link에서는 읽을 수 없습니다.")
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

    def get_snapshot(self) -> TiltLinkSnapshot:
        """장치 I/O 없이 현재 연결 상태를 반환한다."""

        return TiltLinkSnapshot(status=self._status, last_error=self._last_error)

    async def _wait_for_reconnect(self) -> None:
        remaining = self._next_connect_at - asyncio.get_running_loop().time()
        if remaining <= 0:
            return
        LOGGER.debug(
            "틸팅 ESP32 재연결 시각을 기다립니다.",
            extra={"component": "tilt_serial", "event": "tilt_serial_reconnect_wait"},
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
                "틸팅 ESP32 시리얼 포트를 열지 못했습니다.",
                extra={"component": "tilt_serial", "event": "tilt_serial_connect_failed"},
            )
            return False

        if not self._started:
            await self._close_after_cancel(connection)
            return False

        try:
            # 재연결마다 leftover 이동이 없도록 STOP을 가장 먼저 보낸다.
            # write_line()을 거치면 _io_lock을 다시 얻으려 해 교착되므로
            # 여기서는 연결 객체에 직접 쓴다.
            await self._run_blocking(lambda: connection.write(b"STOP\n"))
            await self._run_blocking(connection.flush)
        except EXPECTED_SERIAL_ERRORS as error:
            await self._close_after_cancel(connection)
            self._record_error(error)
            LOGGER.warning(
                "틸팅 ESP32 연결 직후 STOP 전송에 실패했습니다.",
                extra={"component": "tilt_serial", "event": "tilt_serial_initial_stop_failed"},
            )
            return False

        self._connection = connection
        self._status = TiltLinkStatus.CONNECTED
        self._last_error = None
        self._next_connect_at = 0.0
        self._connection_generation += 1
        LOGGER.info(
            "틸팅 ESP32 시리얼 포트에 연결했습니다.",
            extra={"component": "tilt_serial", "event": "tilt_serial_connected"},
        )
        return True

    async def _open_port(self) -> serial.Serial:
        task = asyncio.create_task(
            asyncio.to_thread(
                serial.Serial,
                port=self._settings.serial_port,
                baudrate=self._settings.baudrate,
                timeout=self._settings.read_timeout_seconds,
                write_timeout=self._settings.write_timeout_seconds,
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
            "틸팅 ESP32 시리얼 연결이 끊어졌습니다.",
            extra={"component": "tilt_serial", "event": "tilt_serial_disconnected"},
        )

    def _record_error(self, error: BaseException) -> None:
        self._status = TiltLinkStatus.ERROR
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
        if timeout <= 0:
            raise ValueError("시리얼 read timeout은 양수여야 합니다.")
        return timeout

    @staticmethod
    def _format_error(error: BaseException) -> str:
        detail = str(error).strip()
        return detail or type(error).__name__
