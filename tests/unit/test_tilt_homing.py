"""0단계가 바닥까지 내려 영점을 다시 잡는지 검증한다."""

from __future__ import annotations

from pathlib import Path

import pytest

from smart_desk.modules.tilt.controller import (
    HOMING_MARGIN_MS, HOMING_MAX_DURATION_MS, TiltCommandRejectedError,
)
from smart_desk.modules.tilt.models import TiltState
from tests.unit.test_tilt_controller import build, goto, ready, wait_until


async def started(tmp_path: Path):
    controller, mqtt, link = build(tmp_path)
    await controller.start()
    await ready(controller, link)
    return controller, mqtt, link


def move_lines(link) -> list[str]:  # type: ignore[no-untyped-def]
    return [line for line in link.written if line.startswith(("RUN ", "MOVE_TO ", "SET_POSITION "))]


async def test_level_zero_runs_down_instead_of_moving_to_a_position(tmp_path: Path) -> None:
    controller, _mqtt, link = await started(tmp_path)
    try:
        await controller.set_target(0)
        await wait_until(lambda: any(line.startswith("RUN DOWN") for line in link.written))

        run = next(line for line in link.written if line.startswith("RUN DOWN"))
        duty, duration = run.split()[2], int(run.split()[3])
        assert duty == "100"
        # 전체 행정(38mm ÷ 19mm/s = 2초)에 여유를 더해 바닥까지 확실히 내린다.
        assert duration == 2000 + HOMING_MARGIN_MS
        assert duration <= HOMING_MAX_DURATION_MS
        # 위치 기반 이동은 쓰지 않는다.
        assert not any(line.startswith("MOVE_TO") for line in link.written)
    finally:
        await controller.stop()


async def test_bottom_is_declared_the_new_zero_when_the_run_completes(tmp_path: Path) -> None:
    controller, _mqtt, link = await started(tmp_path)
    try:
        await controller.set_target(0)
        await wait_until(lambda: any(line.startswith("RUN DOWN") for line in link.written))

        link.push({"event": "stopped", "reason": "manual_complete",
                   "firmware": "tilt-test", "position_valid": False})
        await wait_until(lambda: any(line.startswith("SET_POSITION") for line in link.written))
        await wait_until(lambda: controller.get_snapshot().state is TiltState.AT_TARGET)

        snapshot = controller.get_snapshot()
        assert snapshot.level == 0
        assert snapshot.position_mm == 0.0
        assert snapshot.position_valid is True
        assert snapshot.last_error is None
    finally:
        await controller.stop()


async def test_level_zero_is_allowed_even_when_the_position_is_unknown(tmp_path: Path) -> None:
    controller, _mqtt, link = await started(tmp_path)
    try:
        # 수동 정지 등으로 위치만 잃은 상태를 만든다(장치 자체는 멀쩡하다).
        link.push({"event": "stopped", "reason": "user_stop",
                   "firmware": "tilt-test", "position_valid": False})
        await wait_until(lambda: not controller.get_snapshot().position_valid)
        assert controller.get_snapshot().state is TiltState.STOPPED

        # 다른 단계는 막히지만, 0단계는 영점을 되찾는 경로라 허용한다.
        with pytest.raises(TiltCommandRejectedError):
            await controller.set_target(1)
        link.written.clear()
        await controller.set_target(0)
        await wait_until(lambda: any(line.startswith("RUN DOWN") for line in link.written))
    finally:
        await controller.stop()


async def test_level_zero_recovers_a_device_that_booted_without_a_position(tmp_path: Path) -> None:
    """부팅 직후 위치를 모르면 ERROR로 남는다. 이때가 영점을 잡아야 할 때다."""

    controller, _mqtt, link = build(tmp_path)
    await controller.start()
    link.push({"event": "ready", "firmware": "tilt-test", "position_valid": False})
    await wait_until(lambda: controller.get_snapshot().state is TiltState.ERROR)
    try:
        # 다른 단계는 여전히 막는다.
        with pytest.raises(TiltCommandRejectedError):
            await controller.set_target(1)

        await controller.set_target(0)
        await wait_until(lambda: any(line.startswith("RUN DOWN") for line in link.written))
    finally:
        await controller.stop()


async def test_device_without_a_position_homes_itself(tmp_path: Path) -> None:
    """위치를 모른 채 올라오면 사람이 누르지 않아도 스스로 0단계로 내려간다."""

    controller, _mqtt, link = build(tmp_path)
    await controller.start()
    try:
        link.push({"event": "ready", "firmware": "tilt-test", "position_valid": False})
        await wait_until(lambda: any(line.startswith("RUN DOWN") for line in link.written))
    finally:
        await controller.stop()


async def test_auto_homing_is_attempted_once_per_connection(tmp_path: Path) -> None:
    """실패해도 계속 움직이지 않도록 연결 세대마다 한 번만 시도한다."""

    controller, _mqtt, link = build(tmp_path)
    await controller.start()
    try:
        link.push({"event": "ready", "firmware": "tilt-test", "position_valid": False})
        await wait_until(lambda: any(line.startswith("RUN DOWN") for line in link.written))
        first = len([line for line in link.written if line.startswith("RUN DOWN")])

        # 같은 연결에서 다시 위치를 잃어도 자동 하강을 되풀이하지 않는다.
        link.push({"event": "ready", "firmware": "tilt-test", "position_valid": False})
        await wait_until(lambda: controller.get_snapshot().state is not None)
        assert len([line for line in link.written if line.startswith("RUN DOWN")]) == first
    finally:
        await controller.stop()


async def test_device_with_a_known_position_is_left_alone(tmp_path: Path) -> None:
    """위치를 아는 장치는 건드리지 않는다."""

    controller, _mqtt, link = build(tmp_path)
    await controller.start()
    try:
        await ready(controller, link)
        assert not any(line.startswith("RUN DOWN") for line in link.written)
    finally:
        await controller.stop()


async def test_heartbeat_during_a_move_does_not_cancel_it(tmp_path: Path) -> None:
    """이동 중 heartbeat의 position_valid=false는 아직 도착 전이라는 뜻일 뿐이다.

    실제 장치는 RUN이 도는 동안 status를 계속 보낸다. 이걸 준비 실패로 읽으면
    진행 중인 이동이 ERROR로 지워지고, 곧바로 자동 영점 복귀가 다시 돌아
    끝없이 오르내린다.
    """

    controller, _mqtt, link = await started(tmp_path)
    try:
        await controller.set_target(0)
        await wait_until(lambda: controller.get_snapshot().state is TiltState.MOVING)

        link.push({"event": "status", "firmware": "tilt-test", "position_valid": False})
        await wait_until(lambda: controller.get_snapshot().firmware == "tilt-test")

        assert controller.get_snapshot().state is TiltState.MOVING
        assert controller.get_snapshot().last_error is None
    finally:
        await controller.stop()


async def test_other_levels_still_use_position_based_moves(tmp_path: Path) -> None:
    controller, _mqtt, link = await started(tmp_path)
    try:
        await controller.handle_command(goto(1))
        await wait_until(lambda: any(line.startswith("MOVE_TO") for line in link.written))

        assert not any(line.startswith("RUN DOWN") for line in link.written)
    finally:
        await controller.stop()
