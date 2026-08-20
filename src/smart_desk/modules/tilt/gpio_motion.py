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
# lgpio tx_pwm이 받는 최대 주파수. 그 이상은 'bad PWM frequency'로 거부된다.
SOFTWARE_PWM_MAX_HZ = 10000


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
        from smart_desk.modules.gpio_runtime import import_lgpio, open_hardware_pwm

        lgpio = import_lgpio()
        self._lgpio = lgpio
        self._r_en = r_en_pin
        self._l_en = l_en_pin
        self._r_pwm = r_pwm_pin
        self._l_pwm = l_pwm_pin
        self._frequency = pwm_frequency_hz
        self._on_timeout = on_timeout
        self._handle = lgpio.gpiochip_open(chip)

        # 20kHz는 lgpio 소프트웨어 PWM 상한(10kHz)을 넘으므로 하드웨어 PWM을
        # 먼저 시도한다. overlay가 없는 환경에서는 소프트웨어로 물러서되, 그때는
        # 상한에 맞춰 주파수를 낮춰야 tx_pwm이 거부하지 않는다.
        self._hardware_pwm = {
            pin: open_hardware_pwm(pin, pwm_frequency_hz)
            for pin in (self._r_pwm, self._l_pwm)
        }
        if all(self._hardware_pwm.values()):
            claimed = (self._r_en, self._l_en)
        else:
            self._hardware_pwm = {self._r_pwm: None, self._l_pwm: None}
            if pwm_frequency_hz > SOFTWARE_PWM_MAX_HZ:
                LOGGER.warning(
                    "하드웨어 PWM을 쓸 수 없어 소프트웨어 PWM 상한으로 낮춥니다.",
                    extra={
                        "component": "tilt_gpio",
                        "event": "tilt_gpio_pwm_downgraded",
                        "requested_hz": pwm_frequency_hz,
                        "applied_hz": SOFTWARE_PWM_MAX_HZ,
                    },
                )
                self._frequency = SOFTWARE_PWM_MAX_HZ
            claimed = (self._r_en, self._l_en, self._r_pwm, self._l_pwm)

        # enable을 먼저 LOW로 확정한 뒤 PWM 선을 잡는다. 순서가 뒤집히면
        # PWM이 붙는 순간 모터가 튈 수 있다.
        for pin in claimed:
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
        hardware = self._hardware_pwm.get(pin)
        if hardware is not None:
            hardware.set_duty_percent(duty_percent)
            return
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
        for hardware in self._hardware_pwm.values():
            if hardware is not None:
                hardware.close()
        try:
            self._lgpio.gpiochip_close(self._handle)
        except Exception:  # noqa: BLE001 - 종료 경로에서 실패를 삼킨다.
            LOGGER.warning(
                "틸트 GPIO 핸들을 닫지 못했습니다.",
                exc_info=True,
                extra={"component": "tilt_gpio", "event": "tilt_gpio_close_failed"},
            )
