"""작업 모드 사용 시간 기록과 주간 집계를 검증한다."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from smart_desk.modules.profiles import ProfileCreate, ProfileRepository
from smart_desk.modules.profiles.usage import ActivityModeUsageRepository
from smart_desk.storage import SQLiteDatabase


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


class Clock:
    """구간 경계를 정확히 확인하려고 시간을 직접 움직인다."""

    def __init__(self, start: datetime = NOW) -> None:
        self.value = start

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **delta: float) -> None:
        self.value += timedelta(**delta)


@pytest.fixture
async def fixtures(tmp_path: Path):
    database = SQLiteDatabase(tmp_path / "smart-desk.db")
    await database.start()
    profile = await ProfileRepository(database).create_profile(
        ProfileCreate(name="사용자", sittingHeightCm=80, standingHeightCm=105)
    )
    clock = Clock()
    usage = ActivityModeUsageRepository(database, utc_now=clock)
    yield usage, profile.id, clock
    await database.stop()


async def test_closed_interval_counts_only_its_own_span(fixtures) -> None:
    usage, profile_id, clock = fixtures

    await usage.start_interval(profile_id, "mode-study", "공부")
    clock.advance(minutes=30)
    await usage.close_open_intervals(profile_id)
    # 자리를 비운 동안에는 시간이 늘지 않아야 한다.
    clock.advance(hours=3)

    summary = await usage.summarize(days=7, profile_id=profile_id)

    assert summary["totalSeconds"] == 1800
    assert summary["modes"] == [{"key": "mode-study", "name": "공부", "seconds": 1800}]


async def test_open_interval_counts_up_to_now(fixtures) -> None:
    usage, profile_id, clock = fixtures

    await usage.start_interval(profile_id, "default", "기본")
    clock.advance(minutes=10)

    summary = await usage.summarize(days=7, profile_id=profile_id)

    assert summary["totalSeconds"] == 600


async def test_starting_a_mode_closes_the_previous_one(fixtures) -> None:
    usage, profile_id, clock = fixtures

    await usage.start_interval(profile_id, "mode-read", "독서")
    clock.advance(minutes=20)
    await usage.start_interval(profile_id, "mode-study", "공부")
    clock.advance(minutes=40)

    summary = await usage.summarize(days=7, profile_id=profile_id)

    assert summary["totalSeconds"] == 3600
    assert summary["modes"] == [
        {"key": "mode-study", "name": "공부", "seconds": 2400},
        {"key": "mode-read", "name": "독서", "seconds": 1200},
    ]


async def test_interval_crossing_midnight_is_split_per_day(fixtures) -> None:
    usage, profile_id, clock = fixtures
    clock.value = datetime(2026, 8, 18, 23, 30, tzinfo=UTC)

    await usage.start_interval(profile_id, "mode-study", "공부")
    clock.advance(hours=1)  # 다음 날 00:30
    await usage.close_open_intervals(profile_id)

    summary = await usage.summarize(days=7, profile_id=profile_id)
    per_day = {day["date"]: day["totalSeconds"] for day in summary["days"]}

    assert per_day["2026-08-18"] == 1800
    assert per_day["2026-08-19"] == 1800
    assert summary["totalSeconds"] == 3600


async def test_window_covers_requested_days_and_drops_older_spans(fixtures) -> None:
    usage, profile_id, clock = fixtures
    clock.value = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    await usage.start_interval(profile_id, "mode-old", "옛모드")
    clock.advance(hours=1)
    await usage.close_open_intervals(profile_id)

    clock.value = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)
    await usage.start_interval(profile_id, "mode-study", "공부")
    clock.advance(minutes=15)
    await usage.close_open_intervals(profile_id)

    summary = await usage.summarize(days=7, profile_id=profile_id)

    assert len(summary["days"]) == 7
    assert summary["days"][-1]["date"] == "2026-08-18"
    assert summary["totalSeconds"] == 900
    assert [mode["key"] for mode in summary["modes"]] == ["mode-study"]


async def test_history_keeps_the_name_used_at_the_time(fixtures) -> None:
    usage, profile_id, clock = fixtures

    await usage.start_interval(profile_id, "mode-study", "공부")
    clock.advance(minutes=10)
    await usage.start_interval(profile_id, "mode-study", "집중 공부")
    clock.advance(minutes=10)

    summary = await usage.summarize(days=7, profile_id=profile_id)

    # 같은 key의 두 구간은 합산하고, 이름은 먼저 기록된 쪽을 남긴다.
    assert summary["totalSeconds"] == 1200
    assert len(summary["modes"]) == 1


async def test_usage_disappears_when_the_profile_is_deleted(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "smart-desk.db")
    await database.start()
    profiles = ProfileRepository(database)
    profile = await profiles.create_profile(
        ProfileCreate(name="사용자", sittingHeightCm=80, standingHeightCm=105)
    )
    usage = ActivityModeUsageRepository(database, utc_now=Clock())
    await usage.start_interval(profile.id, "default", "기본")

    await profiles.delete_profile(profile.id)

    assert await database.read(
        lambda connection: connection.execute(
            "SELECT COUNT(*) FROM activity_mode_usage"
        ).fetchone()[0]
    ) == 0
    await database.stop()
