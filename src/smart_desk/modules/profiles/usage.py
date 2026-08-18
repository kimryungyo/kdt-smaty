"""작업 모드를 실제로 쓴 시간을 구간으로 기록하고 집계한다.

기록 단위는 반열린 구간 [started_at, ended_at)이다. ended_at이 비어 있으면
지금 진행 중인 구간이고, 집계할 때는 조회 시각까지만 센다. 사용자 session이
끝나면 구간을 닫으므로 자리를 비운 동안에는 시간이 늘지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from sqlite3 import Connection

from smart_desk.storage import SQLiteDatabase


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _to_text(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _from_text(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class ActivityModeUsageRepository:
    """activity_mode_usage table의 SQL과 주간 집계를 소유한다."""

    def __init__(
        self,
        database: SQLiteDatabase,
        *,
        utc_now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._database = database
        self._utc_now = utc_now

    async def start_interval(
        self, profile_id: str, mode_key: str, mode_name: str
    ) -> None:
        """진행 중이던 구간을 닫고 새 모드 구간을 연다."""

        now = _to_text(self._utc_now())

        def write(connection: Connection) -> None:
            _close_open_intervals(connection, profile_id, now)
            connection.execute(
                "INSERT INTO activity_mode_usage "
                "(profile_id, mode_key, mode_name, started_at, ended_at) "
                "VALUES (?, ?, ?, ?, NULL)",
                (profile_id, mode_key, mode_name, now),
            )

        await self._database.write(write)

    async def close_open_intervals(self, profile_id: str | None = None) -> None:
        """열린 구간을 지금 시각으로 닫는다. profile_id가 없으면 전부 닫는다."""

        now = _to_text(self._utc_now())

        def write(connection: Connection) -> None:
            _close_open_intervals(connection, profile_id, now)

        await self._database.write(write)

    async def summarize(self, *, days: int = 7, profile_id: str | None = None) -> dict:
        """최근 `days`일의 모드별·날짜별 사용 시간을 초 단위로 집계한다."""

        now = self._utc_now()
        # 오늘을 포함해 days일을 본다. 경계는 local 자정이 아니라 UTC 자정이다.
        start_day = (now - timedelta(days=days - 1)).date()
        window_start = datetime.combine(start_day, datetime.min.time(), tzinfo=UTC)

        def read(connection: Connection) -> list[tuple[str, str, str, str | None]]:
            query = (
                "SELECT mode_key, mode_name, started_at, ended_at "
                "FROM activity_mode_usage "
                "WHERE (ended_at IS NULL OR ended_at > ?)"
            )
            values: list[object] = [_to_text(window_start)]
            if profile_id is not None:
                query += " AND profile_id = ?"
                values.append(profile_id)
            return [
                (row["mode_key"], row["mode_name"], row["started_at"], row["ended_at"])
                for row in connection.execute(query, values)
            ]

        rows = await self._database.read(read)
        return _summarize_rows(rows, window_start=window_start, now=now, days=days)


def _close_open_intervals(
    connection: Connection, profile_id: str | None, now: str
) -> None:
    query = "UPDATE activity_mode_usage SET ended_at = ? WHERE ended_at IS NULL"
    values: list[object] = [now]
    if profile_id is not None:
        query += " AND profile_id = ?"
        values.append(profile_id)
    # 시계가 뒤로 흔들려도 ended_at < started_at인 구간은 만들지 않는다.
    query += " AND started_at <= ?"
    values.append(now)
    connection.execute(query, values)


def _summarize_rows(
    rows: list[tuple[str, str, str, str | None]],
    *,
    window_start: datetime,
    now: datetime,
    days: int,
) -> dict:
    day_keys = [
        (window_start + timedelta(days=offset)).date() for offset in range(days)
    ]
    per_day: dict[date, dict[str, int]] = {key: {} for key in day_keys}
    names: dict[str, str] = {}
    totals: dict[str, int] = {}

    for mode_key, mode_name, started_text, ended_text in rows:
        started = _from_text(started_text)
        ended = _from_text(ended_text) if ended_text else now
        # 진행 중인 구간은 조회 시각까지만 센다.
        ended = min(ended, now)
        if ended <= started:
            continue
        names.setdefault(mode_key, mode_name)
        for day in day_keys:
            day_start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
            day_end = day_start + timedelta(days=1)
            overlap = min(ended, day_end) - max(started, day_start)
            seconds = int(overlap.total_seconds())
            if seconds <= 0:
                continue
            per_day[day][mode_key] = per_day[day].get(mode_key, 0) + seconds
            totals[mode_key] = totals.get(mode_key, 0) + seconds

    modes = [
        {"key": key, "name": names.get(key, key), "seconds": seconds}
        for key, seconds in sorted(totals.items(), key=lambda item: -item[1])
    ]
    return {
        "from": _to_text(window_start),
        "to": _to_text(now),
        "totalSeconds": sum(totals.values()),
        "modes": modes,
        "days": [
            {
                "date": day.isoformat(),
                "totalSeconds": sum(per_day[day].values()),
                "modes": [
                    {"key": key, "name": names.get(key, key), "seconds": seconds}
                    for key, seconds in sorted(
                        per_day[day].items(), key=lambda item: -item[1]
                    )
                ],
            }
            for day in day_keys
        ],
    }
