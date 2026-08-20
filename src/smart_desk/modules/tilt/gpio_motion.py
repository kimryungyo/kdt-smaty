"""BTS7960 구동 계층. ESP32 `motion_controller.cpp`를 옮긴 것이다.

ESP32에서는 hardware timer ISR이 duration 만료 시 driver enable을 끊었다.
여기서는 asyncio task가 그 역할을 하므로, 프로세스가 살아 있는 동안만 보장된다.
프로세스가 죽는 경우는 atexit 훅으로 덮고, SIGKILL·커널 패닉은 하드웨어 보호가
필요하다.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
from collections.abc import Callable

from smart_desk.modules.tilt.gpio_policy import (
    ABSOLUTE_MAX_MOTION_MS,
    TiltDirection,
)


LOGGER = logging.getLogger(__name__)

MIN_DUTY_PERCENT = 1
MAX_DUTY_PERCENT = 100


class GpioTiltMotion:
    """PWM 두 채널과 enable 두 선으로 틸트 모터를 구동한다."""

    def __init__(
        self,
        r_en_pin: int,
        l_en_pin: int,
        r_pwm_pin: int,
        l_pwm_pin: int,
        pwm_frequency_hz: int,
        on_timeout: Callable[[], None],
        chip: int = 0,
    ) -> None:
        from smart_desk.modules.gpio_runtime import import_lgpio

        lgpio = import_lgpio()
        self._lgpio = lgpio
        self._r_en = r_en_pin
        self._l_en = l_en_pin
        self._r_pwm = r_pwm_pin
        self._l_pwm = l_pwm_pin
        self._frequency = pwm_frequency_hz
        self._on_timeout = on_timeout
        self._handle = lgpio.gpiochip_open(chip)
        # enable을 먼저 LOW로 확정한 뒤 PWM 선을 잡는다. 순서가 뒤집히면
        # PWM이 붙는 순간 모터가 튈 수 있다.
        for pin in (self._r_en, self._l_en, self._r_pwm, self._l_pwm):
            lgpio.gpio_claim_output(self._handle, pin, 0)

        self._direction = TiltDirection.STOP
        self._deadline: asyncio.Task[None] | None = None
        self._closed = False
        atexit.register(self._force_off_safely)

    @property
    def direction(self) -> TiltDirection:
        return self._direction

    def is_moving(self) -> bool:
        return self._direction is not TiltDirection.STOP

    def start(
        self, direction: TiltDirection, duration_ms: int, duty_percent: int
    ) -> bool:
        """지정 방향으로 duration_ms 동안 구동한다. 검증 실패면 False."""

        if (
            direction is TiltDirection.STOP
            or duration_ms <= 0
            or duration_ms > ABSOLUTE_MAX_MOTION_MS
            or duty_percent < MIN_DUTY_PERCENT
            or duty_percent > MAX_DUTY_PERCENT
        ):
            return False

        self._cancel_deadline()
        self._force_off()
        if direction is TiltDirection.UP:
            self._write_pwm(self._r_pwm, duty_percent)
            self._write_pwm(self._l_pwm, 0)
        else:
            self._write_pwm(self._r_pwm, 0)
            self._write_pwm(self._l_pwm, duty_percent)
        # PWM을 세운 뒤에 enable을 올린다.
        self._lgpio.gpio_write(self._handle, self._r_en, 1)
        self._lgpio.gpio_write(self._handle, self._l_en, 1)
        self._direction = direction
        self._deadline = asyncio.create_task(self._expire(duration_ms / 1000.0))
        return True

    def stop(self) -> bool:
        """즉시 정지한다. 반환값은 '이동 중이었는지'다."""

        self._cancel_deadline()
        was_moving = self.is_moving()
        self._force_off()
        return was_moving

    async def _expire(self, delay_seconds: float) -> None:
        try:
            await asyncio.sleep(delay_seconds)
        except asyncio.CancelledError:
            raise
        self._force_off()
        self._on_timeout()

    def _cancel_deadline(self) -> None:
        task, self._deadline = self._deadline, None
        if task is not None and not task.done():
            task.cancel()

    def _force_off(self) -> None:
        """PWM을 0으로 내린 뒤 enable을 끊는다. 원본 force_off와 같은 순서다."""

        self._write_pwm(self._r_pwm, 0)
        self._write_pwm(self._l_pwm, 0)
        self._lgpio.gpio_write(self._handle, self._r_en, 0)
        self._lgpio.gpio_write(self._handle, self._l_en, 0)
        self._direction = TiltDirection.STOP

    def _write_pwm(self, pin: int, duty_percent: int) -> None:
        # lgpio는 duty를 퍼센트(0~100)로 받는다.
        self._lgpio.tx_pwm(self._handle, pin, self._frequency, duty_percent)

    def _force_off_safely(self) -> None:
        try:
            self._force_off()
        except Exception:  # noqa: BLE001 - 여기서 예외를 올리면 OFF를 놓친다.
            LOGGER.error(
                "틸트 모터를 끄지 못했습니다.",
                exc_info=True,
                extra={"component": "tilt_gpio", "event": "tilt_gpio_off_failed"},
            )

    def close(self) -> None:
        if self._closed:
            return
        self._cancel_deadline()
        self._force_off_safely()
        self._closed = True
        atexit.unregister(self._force_off_safely)
        try:
            self._lgpio.gpiochip_close(self._handle)
        except Exception:  # noqa: BLE001 - 종료 경로에서 실패를 삼킨다.
            LOGGER.warning(
                "틸트 GPIO 핸들을 닫지 못했습니다.",
                exc_info=True,
                extra={"component": "tilt_gpio", "event": "tilt_gpio_close_failed"},
            )
