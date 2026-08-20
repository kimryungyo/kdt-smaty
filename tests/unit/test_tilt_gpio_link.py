"""GPIO 틸트 link가 ESP32 펌웨어와 같은 계약을 지키는지 확인한다.

ESP32를 걷어낸 뒤 이 link가 tilt_protocol.cpp / motion_controller.cpp의 역할을
대신하므로, 원본과 같은 이벤트 형식과 위치 추정 규칙을 지켜야 서버 로직이
그대로 동작한다.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest

from smart_desk.config.settings import TiltSettings
from smart_desk.modules.tilt.gpio_policy import (
    TiltDirection,
    make_motion_plan,
    position_allowed,
)


R_EN, L_EN, R_PWM, L_PWM = 22, 23, 12, 13


@pytest.fixture
def pins(monkeypatch: pytest.MonkeyPatch) -> dict[int, tuple[str, int]]:
    """lgpio를 대체해 핀 상태만 기록한다."""

    state: dict[int, tuple[str, int]] = {}
    stub = types.ModuleType("lgpio")
    stub.gpiochip_open = lambda chip: 1  # type: ignore[attr-defined]
    stub.gpio_claim_output = lambda h, pin, level: state.__setitem__(  # type: ignore[attr-defined]
        pin, ("dig", level)
    )
    stub.gpio_write = lambda h, pin, level: state.__setitem__(pin, ("dig", level))  # type: ignore[attr-defined]
    stub.tx_pwm = lambda h, pin, freq, duty: state.__setitem__(pin, ("pwm", duty))  # type: ignore[attr-defined]
    stub.gpiochip_close = lambda h: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lgpio", stub)
    return state


def _settings() -> TiltSettings:
    return TiltSettings(enabled=True, transport="gpio")


async def _drain(link, count: int = 1, timeout: float = 0.3) -> list[dict]:
    events: list[dict] = []
    for _ in range(count):
        raw = await link.read_line(timeout)
        if not raw:
            break
        events.append(json.loads(raw))
    return events


async def _armed(link) -> None:
    """보정 속도를 넣고 원점을 확정해 이동 가능한 상태로 만든다."""

    await link.write_line("CALIBRATE 100 19.5000 UP")
    await link.write_line("CALIBRATE 100 19.2500 DOWN")
    await _drain(link, 2)
    await link.write_line("SET_POSITION 0.00")
    await _drain(link)


def test_motion_plan_matches_firmware_arithmetic() -> None:
    """이동 시간 = 거리/속도*1000 + settle margin(150ms), 올림."""

    plan = make_motion_plan(100.0, True, 150.0, 20.0, 20.0)
    assert plan.direction is TiltDirection.UP
    assert plan.duration_ms == 2650

    # 방향별로 다른 보정 속도를 쓴다.
    plan = make_motion_plan(150.0, True, 100.0, 20.0, 25.0)
    assert plan.direction is TiltDirection.DOWN
    assert plan.duration_ms == 2150


def test_motion_plan_rejects_unusable_input() -> None:
    # 위치가 확정되지 않았으면 계획을 세울 수 없다.
    assert make_motion_plan(100.0, False, 150.0, 20.0, 20.0).direction is TiltDirection.STOP
    # 물리 범위 밖.
    assert make_motion_plan(100.0, True, 250.0, 20.0, 20.0).direction is TiltDirection.STOP
    # 보정 속도가 없으면 거리 계산이 불가능하다.
    assert make_motion_plan(100.0, True, 150.0, 0.0, 20.0).direction is TiltDirection.STOP
    # 상한(16s)을 넘는 계획은 거부한다.
    assert make_motion_plan(0.0, True, 220.0, 0.1, 0.1).direction is TiltDirection.STOP


def test_position_range_matches_actuator_stroke() -> None:
    assert position_allowed(0.0) and position_allowed(220.0)
    assert not position_allowed(-0.1) and not position_allowed(220.1)


@pytest.mark.asyncio
async def test_move_to_completes_and_confirms_position(pins) -> None:
    from smart_desk.modules.tilt.gpio_link import TiltGpioLink

    link = TiltGpioLink(_settings())
    await link.start()
    assert (await _drain(link))[0]["event"] == "ready"
    await _armed(link)

    # 0 -> 1mm @19.5mm/s => 51 + 150 = 202ms
    assert await link.write_line("MOVE_TO 1.00 100")
    moving = (await _drain(link))[0]
    assert moving["event"] == "moving"
    assert moving["direction"] == "UP"
    # UP은 RPWM만 구동하고 두 enable을 올린다.
    assert pins[R_PWM] == ("pwm", 100)
    assert pins[L_PWM] == ("pwm", 0)
    assert pins[R_EN] == ("dig", 1) and pins[L_EN] == ("dig", 1)

    await asyncio.sleep(0.45)
    done = (await _drain(link))[0]
    assert done["event"] == "at_target"
    assert done["position_mm"] == 1.0
    # 만료 시 PWM을 내리고 enable을 끊는다.
    assert pins[R_PWM][1] == 0
    assert pins[R_EN] == ("dig", 0) and pins[L_EN] == ("dig", 0)
    await link.stop()


@pytest.mark.asyncio
async def test_second_command_while_moving_stops_and_rejects(pins) -> None:
    """원본과 같이 busy는 진행 중 이동을 정지시키고 위치를 버린다."""

    from smart_desk.modules.tilt.gpio_link import TiltGpioLink

    link = TiltGpioLink(_settings())
    await link.start()
    await _drain(link)
    await _armed(link)

    await link.write_line("MOVE_TO 1.00 100")
    await _drain(link)
    await link.write_line("MOVE_TO 5.00 100")
    rejected = (await _drain(link))[0]
    assert rejected["event"] == "rejected" and rejected["reason"] == "busy"
    assert pins[R_EN] == ("dig", 0) and pins[L_EN] == ("dig", 0)

    # 취소된 이동의 deadline이 뒤늦게 at_target을 내면 안 된다.
    assert await _drain(link, 2, 0.5) == []

    # 중도 정지 후에는 위치를 신뢰할 수 없다.
    await link.write_line("STATUS")
    status = (await _drain(link))[0]
    assert status["position_valid"] is False
    assert "position_mm" not in status
    await link.stop()


@pytest.mark.asyncio
async def test_manual_run_invalidates_position(pins) -> None:
    """시간 기반 수동 이동은 절대 위치를 주장하지 않는다."""

    from smart_desk.modules.tilt.gpio_link import TiltGpioLink

    link = TiltGpioLink(_settings())
    await link.start()
    await _drain(link)
    await _armed(link)

    assert await link.write_line("RUN UP 100 100")
    moving = (await _drain(link))[0]
    assert moving["event"] == "moving" and moving["position_valid"] is False

    await asyncio.sleep(0.25)
    stopped = (await _drain(link))[0]
    assert stopped["event"] == "stopped"
    assert stopped["reason"] == "manual_complete"
    assert "position_mm" not in stopped
    await link.stop()


@pytest.mark.asyncio
async def test_stop_command_reports_and_invalidates(pins) -> None:
    from smart_desk.modules.tilt.gpio_link import TiltGpioLink

    link = TiltGpioLink(_settings())
    await link.start()
    await _drain(link)
    await _armed(link)

    await link.write_line("MOVE_TO 2.00 100")
    await _drain(link)
    await link.write_line("STOP")
    stopped = (await _drain(link))[0]
    assert stopped["event"] == "stopped" and stopped["reason"] == "command"
    assert stopped["position_valid"] is False
    assert pins[R_EN] == ("dig", 0) and pins[L_EN] == ("dig", 0)
    await link.stop()


@pytest.mark.asyncio
async def test_invalid_commands_are_rejected(pins) -> None:
    from smart_desk.modules.tilt.gpio_link import TiltGpioLink

    link = TiltGpioLink(_settings())
    await link.start()
    await _drain(link)

    for command, reason in (
        ("BOGUS", "unknown_command"),
        ("", "empty_command"),
        ("STOP now", "stop_arguments"),
        ("SET_POSITION 999", "invalid_position"),
        ("MOVE_TO 10.0 0", "invalid_move_to"),
        ("RUN SIDEWAYS 100 100", "invalid_run"),
        ("RUN UP 100 10", "invalid_run"),
    ):
        assert await link.write_line(command)
        event = (await _drain(link))[0]
        assert event["event"] == "rejected", (command, event)
        assert event["reason"] == reason, (command, event)
    await link.stop()


@pytest.mark.asyncio
async def test_stop_releases_gpio(pins) -> None:
    """종료 경로에서 모터가 반드시 꺼져야 한다."""

    from smart_desk.modules.tilt.gpio_link import TiltGpioLink

    link = TiltGpioLink(_settings())
    await link.start()
    await _drain(link)
    await _armed(link)
    await link.write_line("MOVE_TO 5.00 100")
    await _drain(link)

    await link.stop()
    assert pins[R_EN] == ("dig", 0) and pins[L_EN] == ("dig", 0)
    assert pins[R_PWM][1] == 0 and pins[L_PWM][1] == 0
