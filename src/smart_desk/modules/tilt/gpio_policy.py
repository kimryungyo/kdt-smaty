"""틸트 이동 계획 정책. ESP32 `policy.h`를 그대로 옮긴 것이다.

인코더가 없는 개루프 구조라서, 목표까지의 거리와 보정된 속도로 구동 시간을
계산한다. 이 계산이 어긋나면 위치가 그대로 틀어지므로 원본 상수를 유지한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


MIN_POSITION_MM = 0.0
# HW-039 액추에이터의 실제 stroke. 서버 목표와 별개로 지키는 최후 물리 범위다.
MAX_POSITION_MM = 220.0
MOTION_SETTLE_MARGIN_MS = 150
ABSOLUTE_MAX_MOTION_MS = 16000
# 이 거리 미만은 이미 목표로 본다.
AT_TARGET_EPSILON_MM = 0.01


class TiltDirection(StrEnum):
    STOP = "STOP"
    UP = "UP"
    DOWN = "DOWN"


@dataclass(frozen=True, slots=True)
class MotionPlan:
    direction: TiltDirection = TiltDirection.STOP
    duration_ms: int = 0
    at_target: bool = False


def _finite(value: float) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def position_allowed(position_mm: float) -> bool:
    return _finite(position_mm) and MIN_POSITION_MM <= position_mm <= MAX_POSITION_MM


def make_motion_plan(
    current_mm: float,
    position_valid: bool,
    target_mm: float,
    up_speed_mm_s: float,
    down_speed_mm_s: float,
) -> MotionPlan:
    """현재 위치에서 목표까지의 구동 방향과 시간을 정한다.

    반환된 direction이 STOP이고 at_target이 False면 계획을 세울 수 없다는 뜻이다
    (위치 미확정, 범위 밖, 보정 속도 없음, 시간 상한 초과).
    """

    if (
        not position_valid
        or not position_allowed(current_mm)
        or not position_allowed(target_mm)
    ):
        return MotionPlan()

    distance = target_mm - current_mm
    if abs(distance) < AT_TARGET_EPSILON_MM:
        return MotionPlan(at_target=True)

    direction = TiltDirection.UP if distance > 0 else TiltDirection.DOWN
    speed = up_speed_mm_s if direction is TiltDirection.UP else down_speed_mm_s
    if not _finite(speed) or speed <= 0:
        return MotionPlan()

    duration = (abs(distance) / speed) * 1000.0 + MOTION_SETTLE_MARGIN_MS
    if not _finite(duration) or duration <= 0 or duration > ABSOLUTE_MAX_MOTION_MS:
        return MotionPlan()

    return MotionPlan(direction=direction, duration_ms=int(math.ceil(duration)))
