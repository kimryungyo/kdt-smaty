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


async def test_other_levels_still_use_position_based_moves(tmp_path: Path) -> None:
    controller, _mqtt, link = await started(tmp_path)
    try:
        await controller.handle_command(goto(1))
        await wait_until(lambda: any(line.startswith("MOVE_TO") for line in link.written))

        assert not any(line.startswith("RUN DOWN") for line in link.written)
    finally:
        await controller.stop()
