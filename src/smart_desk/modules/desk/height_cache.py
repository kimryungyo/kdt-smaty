"""검증된 ONLINE 높이 관측 하나를 SQLite에 안전하게 보관한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math
from sqlite3 import Connection, Row

from smart_desk.storage import SQLiteDatabase


@dataclass(frozen=True, slots=True)
class CachedHeight:
    height_cm: float
    observed_at: datetime


class HeightCacheRepository:
    """마지막 유효 ONLINE 관측을 단일 row로 저장한다."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    async def load(self) -> CachedHeight | None:
        def read_row(connection: Connection) -> CachedHeight | None:
            row = connection.execute(
                "SELECT height_cm, observed_at FROM desk_height_cache WHERE id = 1"
            ).fetchone()
            return _cached_height_from_row(row) if row is not None else None

        return await self._database.read(read_row)

    async def save(self, height_cm: float, observed_at: datetime) -> None:
        if not math.isfinite(height_cm):
            raise ValueError("저장할 높이는 finite 숫자여야 합니다.")
        if observed_at.tzinfo is None or observed_at.utcoffset() != timedelta(0):
            raise ValueError("저장할 관측 시각은 timezone-aware UTC여야 합니다.")

        def write_row(connection: Connection) -> None:
            connection.execute(
                "INSERT INTO desk_height_cache (id, height_cm, observed_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET height_cm = excluded.height_cm, "
                "observed_at = excluded.observed_at",
                (height_cm, observed_at.isoformat().replace("+00:00", "Z")),
            )

        await self._database.write(write_row)


def _cached_height_from_row(row: Row) -> CachedHeight:
    height_cm = float(row["height_cm"])
    observed_at = datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00"))
    if (
        not math.isfinite(height_cm)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() != timedelta(0)
    ):
        raise ValueError("저장된 높이 cache가 유효하지 않습니다.")
    return CachedHeight(height_cm=height_cm, observed_at=observed_at.astimezone(UTC))
