"""사용자 정의 작업 모드의 SQLite CRUD와 기본 mode 합성을 제공한다."""

from __future__ import annotations

from collections.abc import Callable
import sqlite3
from sqlite3 import Connection, Row
from typing import TypeAlias
from uuid import uuid4

from smart_desk.modules.profiles.models import (
    ActivityMode,
    ActivityModeCreate,
    ActivityModeUpdate,
    EffectiveActivityMode,
    Profile,
)
from smart_desk.modules.profiles.repository import ProfileNotFoundError
from smart_desk.storage import SQLiteDatabase


ActivityModeIdFactory: TypeAlias = Callable[[], str]
ACTIVITY_MODE_SELECT_COLUMNS = (
    "id, profile_id, name, sitting_height_cm, standing_height_cm, led_color, "
    "tilt_level, description"
)
ACTIVITY_MODE_UPDATE_COLUMNS = {
    "name": "name",
    "sitting_height_cm": "sitting_height_cm",
    "standing_height_cm": "standing_height_cm",
    "led_color": "led_color",
    "tilt_level": "tilt_level",
    "description": "description",
}


class ActivityModeRepositoryError(RuntimeError):
    """작업 모드 repository의 기능 오류다."""


class ActivityModeNotFoundError(ActivityModeRepositoryError):
    """요청한 custom 작업 모드가 존재하지 않는다."""


class ActivityModeConflictError(ActivityModeRepositoryError):
    """custom 작업 모드 ID 또는 이름이 충돌했다."""


class ActivityModeOwnershipError(ActivityModeRepositoryError):
    """custom 작업 모드가 요청한 profile의 소유가 아니다."""


def generate_activity_mode_id() -> str:
    """충분히 충돌 가능성이 낮은 server activity mode ID를 만든다."""

    return f"mode-{uuid4().hex}"


def normalize_activity_mode_name(name: str) -> str:
    """표시 이름의 profile별 중복 판정 key를 만든다."""

    return name.strip().casefold()


class ActivityModeRepository:
    """profile_modes SQL, custom CRUD와 기본 mode 합성을 소유한다."""

    def __init__(
        self,
        database: SQLiteDatabase,
        *,
        id_factory: ActivityModeIdFactory = generate_activity_mode_id,
    ) -> None:
        self._database = database
        self._id_factory = id_factory

    async def list_effective_modes(self, profile_id: str) -> list[EffectiveActivityMode]:
        def list_rows(connection: Connection) -> list[EffectiveActivityMode]:
            profile = _get_profile_or_raise(connection, profile_id)
            rows = connection.execute(
                f"SELECT {ACTIVITY_MODE_SELECT_COLUMNS} FROM profile_modes "
                "WHERE profile_id = ? ORDER BY name COLLATE NOCASE, id",
                (profile_id,),
            ).fetchall()
            return [_default_mode_from_profile(profile)] + [
                _effective_mode_from_row(row) for row in rows
            ]

        return await self._database.read(list_rows)

    async def create_mode(
        self, profile_id: str, create: ActivityModeCreate
    ) -> ActivityMode:
        mode_id = self._id_factory()
        normalized_name = normalize_activity_mode_name(create.name)

        def insert(connection: Connection) -> ActivityMode:
            _get_profile_or_raise(connection, profile_id)
            try:
                connection.execute(
                    "INSERT INTO profile_modes "
                    "(id, profile_id, name, normalized_name, sitting_height_cm, "
                    "standing_height_cm, led_color, tilt_level, description) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        mode_id,
                        profile_id,
                        create.name,
                        normalized_name,
                        create.sitting_height_cm,
                        create.standing_height_cm,
                        create.led_color,
                        create.tilt_level,
                        create.description,
                    ),
                )
            except sqlite3.IntegrityError as error:
                if _is_mode_id_conflict(error):
                    raise ActivityModeConflictError("이미 존재하는 작업 모드 ID가 생성되었습니다.") from error
                if _is_mode_name_conflict(error):
                    raise ActivityModeConflictError("같은 프로필에 이미 같은 작업 모드 이름이 있습니다.") from error
                raise
            return _get_mode_or_raise(connection, mode_id)

        return await self._database.write(insert)

    async def update_mode(self, mode_id: str, update: ActivityModeUpdate) -> ActivityMode:
        changes = update.model_dump(exclude_unset=True, by_alias=False)
        if "name" in changes:
            changes["normalized_name"] = normalize_activity_mode_name(changes["name"])

        def update_row(connection: Connection) -> ActivityMode:
            _get_mode_or_raise(connection, mode_id)
            assignments: list[str] = []
            values: list[object] = []
            for field_name, column_name in ACTIVITY_MODE_UPDATE_COLUMNS.items():
                if field_name in changes:
                    assignments.append(f"{column_name} = ?")
                    values.append(changes[field_name])
            if "normalized_name" in changes:
                assignments.append("normalized_name = ?")
                values.append(changes["normalized_name"])
            try:
                connection.execute(
                    f"UPDATE profile_modes SET {', '.join(assignments)} WHERE id = ?",
                    [*values, mode_id],
                )
            except sqlite3.IntegrityError as error:
                if _is_mode_name_conflict(error):
                    raise ActivityModeConflictError("같은 프로필에 이미 같은 작업 모드 이름이 있습니다.") from error
                raise
            return _get_mode_or_raise(connection, mode_id)

        return await self._database.write(update_row)

    async def delete_mode(self, mode_id: str) -> None:
        def delete_row(connection: Connection) -> None:
            cursor = connection.execute("DELETE FROM profile_modes WHERE id = ?", (mode_id,))
            if cursor.rowcount == 0:
                raise ActivityModeNotFoundError("요청한 작업 모드를 찾을 수 없습니다.")

        await self._database.write(delete_row)

    async def get_mode_for_profile(self, profile_id: str, mode_id: str) -> ActivityMode:
        """후속 자동화가 custom mode의 profile 소유권을 확인할 때 사용한다."""

        def get_row(connection: Connection) -> ActivityMode:
            _get_profile_or_raise(connection, profile_id)
            mode = _get_mode_or_raise(connection, mode_id)
            if mode.profile_id != profile_id:
                raise ActivityModeOwnershipError("요청한 프로필의 작업 모드가 아닙니다.")
            return mode

        return await self._database.read(get_row)


def _get_profile_or_raise(connection: Connection, profile_id: str) -> Profile:
    row = connection.execute(
        "SELECT id, name, sitting_height_cm, standing_height_cm, led_color, "
        "tilt_level, description FROM profiles WHERE id = ?",
        (profile_id,),
    ).fetchone()
    if row is None:
        raise ProfileNotFoundError("요청한 프로필을 찾을 수 없습니다.")
    return Profile.model_validate(dict(row))


def _get_mode_or_raise(connection: Connection, mode_id: str) -> ActivityMode:
    row = connection.execute(
        f"SELECT {ACTIVITY_MODE_SELECT_COLUMNS} FROM profile_modes WHERE id = ?",
        (mode_id,),
    ).fetchone()
    if row is None:
        raise ActivityModeNotFoundError("요청한 작업 모드를 찾을 수 없습니다.")
    return _activity_mode_from_row(row)


def _activity_mode_from_row(row: Row) -> ActivityMode:
    return ActivityMode.model_validate(dict(row))


def _default_mode_from_profile(profile: Profile) -> EffectiveActivityMode:
    return EffectiveActivityMode(
        key="default",
        kind="DEFAULT",
        name="기본",
        sitting_height_cm=profile.sitting_height_cm,
        standing_height_cm=profile.standing_height_cm,
        led_color=profile.led_color,
        tilt_level=profile.tilt_level,
        description=profile.description,
        editable=False,
    )


def _effective_mode_from_row(row: Row) -> EffectiveActivityMode:
    return effective_mode_from_activity(_activity_mode_from_row(row))


def effective_mode_from_activity(mode: ActivityMode) -> EffectiveActivityMode:
    """저장 custom row를 공개 합성 표현으로 바꾼다."""

    return EffectiveActivityMode(
        key=mode.id,
        kind="CUSTOM",
        name=mode.name,
        sitting_height_cm=mode.sitting_height_cm,
        standing_height_cm=mode.standing_height_cm,
        led_color=mode.led_color,
        tilt_level=mode.tilt_level,
        description=mode.description,
        editable=True,
    )


def _is_mode_id_conflict(error: sqlite3.IntegrityError) -> bool:
    return getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY


def _is_mode_name_conflict(error: sqlite3.IntegrityError) -> bool:
    return getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE
