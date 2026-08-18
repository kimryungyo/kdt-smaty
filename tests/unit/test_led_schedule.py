"""시간에 따른 조명 스케줄이 올바른 구간을 고르는지 확인한다."""

from __future__ import annotations

from datetime import time

import pytest

from smart_desk.modules.profiles.led_schedule import (
    DEFAULT_STUDY_SCHEDULE,
    DEFAULT_TIME_OF_DAY_SCHEDULE,
    parse_schedule,
    schedule_to_raw,
)


@pytest.mark.parametrize(("clock", "expected"), [
    (time(7, 0), ("FFCB8D", 102)),     # 아침 3500K
    (time(9, 59), ("FFCB8D", 102)),    # 아직 아침 구간
    (time(10, 0), ("FFE8C3", 204)),    # 오전 5000K
    (time(13, 0), ("FFF6D8", 255)),    # 낮 6000K
    (time(17, 59), ("FFF6D8", 255)),
    (time(18, 0), ("FFE0B5", 140)),    # 저녁 4500K
    (time(22, 0), ("FFBD70", 77)),     # 밤 3000K
    (time(23, 30), ("FFBD70", 77)),
    (time(3, 0), ("FFBD70", 77)),      # 자정을 넘겨도 밤 구간이 이어진다
    (time(6, 59), ("FFBD70", 77)),
])
def test_time_of_day_picks_the_step_for_that_hour(clock, expected) -> None:
    schedule = parse_schedule(DEFAULT_TIME_OF_DAY_SCHEDULE)
    assert schedule is not None
    assert schedule.resolve(now=clock) == expected


@pytest.mark.parametrize(("minutes", "expected"), [
    (0, ("FFD6A4", 153)),      # 앉자마자 4000K
    (3.9, ("FFD6A4", 153)),
    (4, ("FFE0B5", 179)),      # 4분 4500K
    (7, ("FFE0B5", 179)),
    (8, ("FFE8C3", 217)),      # 8분 5000K
    (10, ("FFF6D8", 255)),     # 10분부터 6000K
    (90, ("FFF6D8", 255)),     # 그 뒤로는 유지된다
])
def test_elapsed_ramps_up_while_the_mode_stays_on(minutes, expected) -> None:
    schedule = parse_schedule(DEFAULT_STUDY_SCHEDULE)
    assert schedule is not None
    assert schedule.resolve(elapsed_minutes=minutes) == expected


def test_schedule_survives_a_round_trip() -> None:
    schedule = parse_schedule(DEFAULT_TIME_OF_DAY_SCHEDULE)
    assert parse_schedule(schedule_to_raw(schedule)) == schedule


def test_brightness_never_falls_to_darkness() -> None:
    """바탕 조명은 늘 켜 둔다. 0을 넣어도 최소 밝기로 올라온다."""

    schedule = parse_schedule(
        {"kind": "ELAPSED", "steps": [{"at": 0, "color": "FFFFFF", "brightness": 0}]}
    )
    assert schedule is not None
    _, brightness = schedule.resolve(elapsed_minutes=0)
    assert brightness > 0


@pytest.mark.parametrize("broken", [
    {"kind": "NOPE", "steps": [{"at": 0, "color": "FFFFFF", "brightness": 10}]},
    {"kind": "ELAPSED", "steps": []},
    {"kind": "ELAPSED", "steps": [{"at": 0, "color": "GGGGGG", "brightness": 10}]},
    {"kind": "ELAPSED", "steps": [{"at": 0, "color": "FFFFFF", "brightness": 300}]},
    {"kind": "TIME_OF_DAY", "steps": [{"at": "25:00", "color": "FFFFFF", "brightness": 10}]},
])
def test_broken_schedules_are_rejected(broken) -> None:
    with pytest.raises((ValueError, KeyError)):
        parse_schedule(broken)
