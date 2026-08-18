"""profiles table의 SQL과 Pydantic row 변환을 소유한다."""

from __future__ import annotations

from collections.abc import Callable
import sqlite3
from sqlite3 import Connection, Row
from typing import TypeAlias
from uuid import uuid4

from smart_desk.modules.profiles.models import Profile, ProfileCreate, ProfileUpdate
from smart_desk.modules.profiles.led_schedule import (
    decode_schedule as _decode_schedule,
    encode_schedule as _encode_schedule,
)
from smart_desk.storage import SQLiteDatabase


ProfileIdFactory: TypeAlias = Callable[[], str]

PROFILE_SELECT_COLUMNS = (
    "id, name, sitting_height_cm, standing_height_cm, led_color, "
    "led_brightness, led_schedule, tilt_level, description, pin_hash"
)
PROFILE_UPDATE_COLUMNS = {
    "name": "name",
    "sitting_height_cm": "sitting_height_cm",
    "standing_height_cm": "standing_height_cm",
    "led_color": "led_color",
    "led_brightness": "led_brightness",
    "led_schedule": "led_schedule",
    "tilt_level": "tilt_level",
    "description": "description",
}


class ProfileRepositoryError(RuntimeError):
    """프로필 repository의 기능 오류다."""


class ProfileNotFoundError(ProfileRepositoryError):
    """요청한 ID의 프로필이 존재하지 않는다."""


class ProfileConflictError(ProfileRepositoryError):
    """새 프로필의 server ID가 기존 ID와 충돌했다."""


def generate_profile_id() -> str:
    """충분히 충돌 가능성이 낮은 server profile ID를 만든다."""

    return f"profile-{uuid4().hex}"


class ProfileRepository:
    """프로필 CRUD를 SQLiteDatabase transaction 경계에서 수행한다."""

    def __init__(
        self,
        database: SQLiteDatabase,
        *,
        id_factory: ProfileIdFactory = generate_profile_id,
    ) -> None:
        self._database = database
        self._id_factory = id_factory

    async def list_profiles(self) -> list[Profile]:
        def list_rows(connection: Connection) -> list[Profile]:
            rows = connection.execute(
                f"SELECT {PROFILE_SELECT_COLUMNS} FROM profiles "
                "ORDER BY name COLLATE NOCASE, id"
            ).fetchall()
            return [_profile_from_row(row) for row in rows]

        return await self._database.read(list_rows)

    async def get_profile(self, profile_id: str) -> Profile:
        def get_row(connection: Connection) -> Profile:
            return _get_profile_or_raise(connection, profile_id)

        return await self._database.read(get_row)

    async def create_profile(self, create: ProfileCreate) -> Profile:
        profile_id = self._id_factory()

        def insert(connection: Connection) -> Profile:
            try:
                connection.execute(
                    "INSERT INTO profiles "
                    "(id, name, sitting_height_cm, standing_height_cm, led_color, "
                    "led_brightness, led_schedule, tilt_level, description) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        profile_id,
                        create.name,
                        create.sitting_height_cm,
                        create.standing_height_cm,
                        create.led_color,
                        create.led_brightness,
                        _encode_schedule(create.led_schedule),
                        create.tilt_level,
                        create.description,
                    ),
                )
            except sqlite3.IntegrityError as error:
                if _is_profile_id_conflict(error):
                    raise ProfileConflictError(
                        "이미 존재하는 프로필 ID가 생성되었습니다."
                    ) from error
                raise
            return _get_profile_or_raise(connection, profile_id)

        return await self._database.write(insert)

    async def update_profile(
        self,
        profile_id: str,
        update: ProfileUpdate,
    ) -> Profile:
        changes = update.model_dump(exclude_unset=True, by_alias=False)
        # 색이나 밝기를 직접 고르면 그 값으로 고정한다. 스케줄을 남겨 두면
        # 다음 구간에서 곧바로 덮어써, 고른 값이 잠깐만 보이고 사라진다.
        if ("led_schedule" not in changes
                and ("led_color" in changes or "led_brightness" in changes)):
            changes["led_schedule"] = None

        def update_row(connection: Connection) -> Profile:
            assignments: list[str] = []
            values: list[object] = []
            for field_name, column_name in PROFILE_UPDATE_COLUMNS.items():
                if field_name in changes:
                    assignments.append(f"{column_name} = ?")
                    value = changes[field_name]
                    values.append(
                        _encode_schedule(value) if field_name == "led_schedule" else value
                    )
            values.append(profile_id)
            cursor = connection.execute(
                f"UPDATE profiles SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            if cursor.rowcount == 0:
                raise ProfileNotFoundError("요청한 프로필을 찾을 수 없습니다.")
            return _get_profile_or_raise(connection, profile_id)

        return await self._database.write(update_row)

    async def get_pin_hash(self, profile_id: str) -> str | None:
        """저장된 PIN 해시를 반환한다. 이 값은 API 응답에 포함하지 않는다."""

        def read_hash(connection: Connection) -> str | None:
            row = connection.execute(
                "SELECT pin_hash FROM profiles WHERE id = ?",
                (profile_id,),
            ).fetchone()
            if row is None:
                raise ProfileNotFoundError("요청한 프로필을 찾을 수 없습니다.")
            return row["pin_hash"]

        return await self._database.read(read_hash)

    async def set_pin_hash(self, profile_id: str, pin_hash: str | None) -> None:
        """PIN 해시를 저장하거나 None으로 잠금을 해제한다."""

        def write_hash(connection: Connection) -> None:
            cursor = connection.execute(
                "UPDATE profiles SET pin_hash = ? WHERE id = ?",
                (pin_hash, profile_id),
            )
            if cursor.rowcount == 0:
                raise ProfileNotFoundError("요청한 프로필을 찾을 수 없습니다.")

        await self._database.write(write_hash)

    async def delete_profile(self, profile_id: str) -> None:
        def delete_row(connection: Connection) -> None:
            cursor = connection.execute(
                "DELETE FROM profiles WHERE id = ?",
                (profile_id,),
            )
            if cursor.rowcount == 0:
                raise ProfileNotFoundError("요청한 프로필을 찾을 수 없습니다.")

        await self._database.write(delete_row)


def _get_profile_or_raise(connection: Connection, profile_id: str) -> Profile:
    row = connection.execute(
        f"SELECT {PROFILE_SELECT_COLUMNS} FROM profiles WHERE id = ?",
        (profile_id,),
    ).fetchone()
    if row is None:
        raise ProfileNotFoundError("요청한 프로필을 찾을 수 없습니다.")
    return _profile_from_row(row)


def _profile_from_row(row: Row) -> Profile:
    return Profile.model_validate(
        {
            "id": row["id"],
            "name": row["name"],
            "sitting_height_cm": row["sitting_height_cm"],
            "standing_height_cm": row["standing_height_cm"],
            "led_color": row["led_color"],
            "led_brightness": row["led_brightness"],
            "led_schedule": _decode_schedule(row["led_schedule"]),
            "tilt_level": row["tilt_level"],
            "description": row["description"],
            # PIN 해시 자체는 공개하지 않고 잠금 여부만 노출한다.
            "has_pin": row["pin_hash"] is not None,
        }
    )


def _is_profile_id_conflict(error: sqlite3.IntegrityError) -> bool:
    return getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY
