"""라즈베리파이 GPIO로 책상 릴레이를 직접 구동한다.

ESP32 relay 보드를 걷어낸 뒤의 RelayClient 대체 구현이다. 공개 계약(pulse/
send_stop/wake/get_snapshot)은 그대로 두어 DeskController를 손대지 않는다.

ESP32에서는 hardware timer ISR이 hold 만료를 보장했다. 리눅스에는 그런 보장이
없으므로, 여기서는 asyncio watchdog task가 만료 시각에 GPIO를 끈다. 서버가
살아 있는 한의 보장이며, 프로세스가 죽는 경우는 아래 stop()/atexit로 덮는다.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
from datetime import UTC, datetime

from smart_desk.modules.desk.models import (
    Direction,
    RelayEvent,
    RelaySnapshot,
    RelayState,
)


LOGGER = logging.getLogger(__name__)

FIRMWARE = "rpi-gpio-relay-1.0.0"

# ESP32 policy.h와 같은 상한을 유지한다. DeskController가 이 범위로 보낸다.
MIN_HOLD_MS = 50
MAX_HOLD_MS = 500
# 방향 전환 시 두 릴레이가 겹치지 않도록 하는 break-before-make 간격.
BREAK_BEFORE_MAKE_SECONDS = 0.05


class GpioRelayClient:
    """RelayClient와 같은 계약을 GPIO 출력으로 구현한다."""

    def __init__(self, up_pin: int, down_pin: int, chip: int = 0) -> None:
        from smart_desk.modules.gpio_runtime import import_lgpio

        lgpio = import_lgpio()
        self._lgpio = lgpio
        self._up_pin = up_pin
        self._down_pin = down_pin
        self._handle = lgpio.gpiochip_open(chip)
        # active-high. 열자마자 두 선을 LOW로 확정해 부팅 잔여 상태를 지운다.
        for pin in (self._up_pin, self._down_pin):
            lgpio.gpio_claim_output(self._handle, pin, 0)

        self._direction: RelayState = RelayState.STOP
        self._deadline_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._snapshot = self._build_snapshot(
            RelayEvent.ONLINE, "ready", "GPIO 릴레이가 준비되었습니다."
        )
        # 정상 종료 경로(stop)를 타지 못하는 예외 종료에서도 릴레이를 끈다.
        # SIGKILL과 커널 패닉은 이 훅으로도 막지 못하므로 하드웨어 보호가 필요하다.
        atexit.register(self._write_off_safely)

    async def start(self) -> None:
        """컨테이너 lifecycle 계약. GPIO는 __init__에서 이미 확보했다."""

        return None

    async def stop(self) -> None:
        """종료 순서에 따라 릴레이를 끄고 GPIO를 반납한다."""

        await self.close()

    async def pulse(self, direction: Direction, hold_ms: int) -> None:
        """UP/DOWN을 켜고 hold_ms 뒤 자동으로 끄는 deadline을 건다."""

        if not isinstance(direction, Direction):
            raise TypeError(
                "릴레이 pulse 방향은 Direction.UP 또는 Direction.DOWN이어야 합니다."
            )
        if isinstance(hold_ms, bool) or not isinstance(hold_ms, int):
            raise TypeError("릴레이 hold_ms는 bool이 아닌 정수여야 합니다.")
        if not MIN_HOLD_MS <= hold_ms <= MAX_HOLD_MS:
            raise ValueError(
                f"릴레이 hold_ms는 {MIN_HOLD_MS}~{MAX_HOLD_MS}ms여야 합니다."
            )

        async with self._lock:
            await self._engage(RelayState(direction.value), hold_ms)

    async def send_stop(self) -> None:
        """즉시 두 릴레이를 끈다. 항상 허용되는 안전 명령이다."""

        async with self._lock:
            self._cancel_deadline()
            self._write_off()
            self._direction = RelayState.STOP
            self._snapshot = self._build_snapshot(
                RelayEvent.ONLINE, "ready", "릴레이를 정지했습니다."
            )

    async def wake(self, direction: Direction, basis_height_cm: float) -> None:
        """높이 센서를 깨우는 400ms pulse. ESP32 WAKE와 같은 역할이다."""

        if not isinstance(direction, Direction):
            raise TypeError("WAKE 방향은 Direction.UP 또는 Direction.DOWN이어야 합니다.")
        async with self._lock:
            await self._engage(RelayState(direction.value), 400)

    def get_snapshot(self) -> RelaySnapshot:
        """현재 릴레이 상태를 반환한다.

        GPIO는 wire가 없어 heartbeat가 오지 않는다. 상태는 이 프로세스가 곧
        진실이므로, DeskController의 stale 판정을 통과하도록 조회 시점을
        received_at으로 준다.
        """

        snapshot = self._snapshot
        return RelaySnapshot(
            event=snapshot.event,
            state=self._direction,
            firmware=snapshot.firmware,
            code=snapshot.code,
            detail=snapshot.detail,
            received_at=datetime.now(UTC),
            last_error=snapshot.last_error,
        )

    async def close(self) -> None:
        """종료 경로에서 릴레이를 끄고 GPIO를 반납한다."""

        if self._closed:
            return
        self._cancel_deadline()
        self._write_off_safely()
        self._closed = True
        atexit.unregister(self._write_off_safely)
        try:
            self._lgpio.gpiochip_close(self._handle)
        except Exception:  # noqa: BLE001 - 종료 경로에서 실패를 삼킨다.
            LOGGER.warning(
                "GPIO 핸들을 닫지 못했습니다.",
                exc_info=True,
                extra={"component": "relay", "event": "relay_gpio_close_failed"},
            )

    def _write_off_safely(self) -> None:
        """어떤 종료 경로에서도 실패하지 않는 OFF. atexit에서도 쓴다."""

        try:
            self._write_off()
        except Exception:  # noqa: BLE001 - 여기서 예외를 올리면 OFF를 놓친다.
            LOGGER.error(
                "릴레이를 끄지 못했습니다.",
                exc_info=True,
                extra={"component": "relay", "event": "relay_gpio_off_failed"},
            )

    async def _engage(self, direction: RelayState, hold_ms: int) -> None:
        self._cancel_deadline()
        if self._direction is not direction and self._direction is not RelayState.STOP:
            # 방향 전환: 두 릴레이가 동시에 붙지 않도록 반드시 먼저 끈다.
            self._write_off()
            await asyncio.sleep(BREAK_BEFORE_MAKE_SECONDS)
        self._write_off()
        pin = self._up_pin if direction is RelayState.UP else self._down_pin
        self._lgpio.gpio_write(self._handle, pin, 1)
        self._direction = direction
        self._snapshot = self._build_snapshot(
            RelayEvent.ONLINE, "ready", "릴레이 제어기가 준비되었습니다."
        )
        self._deadline_task = asyncio.create_task(self._expire(hold_ms / 1000.0))

    async def _expire(self, delay_seconds: float) -> None:
        """hold 만료 시 GPIO를 끈다. ESP32 timer ISR을 대신하는 경로다."""

        try:
            await asyncio.sleep(delay_seconds)
        except asyncio.CancelledError:
            raise
        # 여기서는 _lock을 잡지 않는다. 만료 정지는 어떤 명령보다 우선한다.
        self._write_off()
        self._direction = RelayState.STOP
        self._snapshot = self._build_snapshot(
            RelayEvent.STOPPED, "ready", "hold 시간이 만료되어 정지했습니다."
        )

    def _cancel_deadline(self) -> None:
        task, self._deadline_task = self._deadline_task, None
        if task is not None and not task.done():
            task.cancel()

    def _write_off(self) -> None:
        for pin in (self._up_pin, self._down_pin):
            self._lgpio.gpio_write(self._handle, pin, 0)

    def _build_snapshot(
        self, event: RelayEvent, code: str, detail: str
    ) -> RelaySnapshot:
        return RelaySnapshot(
            event=event,
            state=self._direction,
            firmware=FIRMWARE,
            code=code,
            detail=detail,
            received_at=datetime.now(UTC),
            last_error=None,
        )
