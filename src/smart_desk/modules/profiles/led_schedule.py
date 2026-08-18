"""시간에 따라 조명 색과 밝기를 바꾸는 규칙.

두 가지 기준이 있다.

- ``TIME_OF_DAY``: 벽시계 기준. 하루를 몇 구간으로 나눠 아침엔 낮은 색온도로,
  낮엔 높은 색온도로 간다. 마지막 구간은 자정을 넘겨 다음 첫 구간까지 이어진다.
- ``ELAPSED``: 그 모드를 켠 뒤 흐른 시간 기준. 공부 모드처럼 앉아 있는 동안
  서서히 집중 쪽으로 올리는 데 쓴다. 마지막 구간은 계속 유지된다.

색온도와 밝기는 같은 방향으로 움직인다(Kruithof). 높은 색온도일수록 밝고 낮은
색온도일수록 어둡다. 반대로 가면 어색하게 느껴진다.

밝기는 0이 되지 않는다. 작업면만 밝히고 주변을 끄면 대비가 커져 눈이 쉽게
피로해지므로, 가장 어두운 밤 구간에도 바탕은 켜 둔다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import time
from typing import Any, Literal


TIME_OF_DAY = "TIME_OF_DAY"
ELAPSED = "ELAPSED"
ScheduleKind = Literal["TIME_OF_DAY", "ELAPSED"]

# 밝기를 끝까지 내려도 이 아래로는 두지 않는다. 바탕 조명은 늘 켜 둔다.
MIN_BRIGHTNESS = 26


@dataclass(frozen=True, slots=True)
class ScheduleStep:
    """한 구간. `at`은 TIME_OF_DAY면 분 단위 시각, ELAPSED면 경과 분이다."""

    at: int
    color: str
    brightness: int


@dataclass(frozen=True, slots=True)
class LedSchedule:
    kind: ScheduleKind
    steps: tuple[ScheduleStep, ...]

    def resolve(self, *, now: time | None = None, elapsed_minutes: float | None = None
                ) -> tuple[str, int] | None:
        """지금 적용할 (색, 밝기)를 고른다. 정할 수 없으면 None."""

        if not self.steps:
            return None
        if self.kind == ELAPSED:
            if elapsed_minutes is None:
                return None
            position = max(0, int(elapsed_minutes))
            # 마지막 구간에 도달하면 그대로 유지한다.
            chosen = self.steps[0]
            for step in self.steps:
                if position >= step.at:
                    chosen = step
            return chosen.color, chosen.brightness
        if now is None:
            return None
        minutes = now.hour * 60 + now.minute
        # 첫 구간보다 이르면 아직 어제 마지막 구간이다(자정을 넘겨 이어진다).
        chosen = self.steps[-1]
        for step in self.steps:
            if minutes >= step.at:
                chosen = step
        return chosen.color, chosen.brightness


def _step_from(raw: Any, kind: str) -> ScheduleStep:
    at = raw["at"]
    if kind == TIME_OF_DAY and isinstance(at, str):
        hour, _, minute = at.partition(":")
        at = int(hour) * 60 + int(minute)
    at = int(at)
    if at < 0:
        raise ValueError("스케줄 시점은 음수일 수 없습니다.")
    if kind == TIME_OF_DAY and at >= 24 * 60:
        raise ValueError("시각 스케줄은 하루를 넘을 수 없습니다.")
    color = str(raw["color"]).upper()
    if len(color) != 6 or any(character not in "0123456789ABCDEF" for character in color):
        raise ValueError("스케줄 색상은 6자리 hexadecimal이어야 합니다.")
    brightness = int(raw["brightness"])
    if not 0 <= brightness <= 255:
        raise ValueError("스케줄 밝기는 0에서 255 사이여야 합니다.")
    return ScheduleStep(at=at, color=color, brightness=max(brightness, MIN_BRIGHTNESS))


def parse_schedule(raw: Any) -> LedSchedule | None:
    """저장된 표현을 스케줄로 바꾼다. 비어 있으면 None."""

    if raw is None:
        return None
    kind = str(raw["kind"]).upper()
    if kind not in (TIME_OF_DAY, ELAPSED):
        raise ValueError("스케줄 종류가 올바르지 않습니다.")
    steps = tuple(sorted(
        (_step_from(item, kind) for item in raw["steps"]), key=lambda step: step.at
    ))
    if not steps:
        raise ValueError("스케줄에는 구간이 하나 이상 있어야 합니다.")
    return LedSchedule(kind=kind, steps=steps)  # type: ignore[arg-type]


def schedule_to_raw(schedule: LedSchedule | None) -> dict[str, Any] | None:
    """저장·전송용 표현으로 되돌린다."""

    if schedule is None:
        return None
    return {
        "kind": schedule.kind,
        "steps": [
            {"at": step.at, "color": step.color, "brightness": step.brightness}
            for step in schedule.steps
        ],
    }


# 논문에서 가져온 기본값이다. 시각별 색온도는 3500K/5000K/6000K/4500K/3000K,
# 밝기는 색온도와 같은 방향으로 둔다. 07시(3500K)는 밤(3000K, 30%)과
# 저녁(4500K, 55%) 사이라 40%로 잡았다.
DEFAULT_TIME_OF_DAY_SCHEDULE: dict[str, Any] = {
    "kind": TIME_OF_DAY,
    "steps": [
        {"at": "07:00", "color": "FFCB8D", "brightness": 102},   # 3500K · 40%
        {"at": "10:00", "color": "FFE8C3", "brightness": 204},   # 5000K · 80%
        {"at": "13:00", "color": "FFF6D8", "brightness": 255},   # 6000K · 100%
        {"at": "18:00", "color": "FFE0B5", "brightness": 140},   # 4500K · 55%
        {"at": "22:00", "color": "FFBD70", "brightness": 77},    # 3000K · 30%
    ],
}

# 공부 모드는 앉은 뒤 흐른 시간에 따라 집중 쪽으로 올라간다.
DEFAULT_STUDY_SCHEDULE: dict[str, Any] = {
    "kind": ELAPSED,
    "steps": [
        {"at": 0, "color": "FFD6A4", "brightness": 153},         # 4000K · 60%
        {"at": 4, "color": "FFE0B5", "brightness": 179},         # 4500K · 70%
        {"at": 8, "color": "FFE8C3", "brightness": 217},         # 5000K · 85%
        {"at": 10, "color": "FFF6D8", "brightness": 255},        # 6000K · 100%
    ],
}


def encode_schedule(raw: dict[str, Any] | None) -> str | None:
    """저장용 JSON 문자열로 바꾼다. SQLite는 dict를 그대로 받지 못한다."""

    return None if raw is None else json.dumps(raw, ensure_ascii=False)


def decode_schedule(stored: Any) -> dict[str, Any] | None:
    """저장된 문자열을 되읽는다. 깨져 있으면 스케줄이 없는 것으로 본다."""

    if stored is None:
        return None
    if isinstance(stored, dict):
        return stored
    try:
        return schedule_to_raw(parse_schedule(json.loads(str(stored))))
    except Exception:
        return None
