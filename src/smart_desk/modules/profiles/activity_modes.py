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
from smart_desk.modules.profiles.led_schedule import decode_schedule, encode_schedule
from smart_desk.modules.profiles.repository import ProfileNotFoundError
from smart_desk.storage import SQLiteDatabase


ActivityModeIdFactory: TypeAlias = Callable[[], str]
ACTIVITY_MODE_SELECT_COLUMNS = (
    "id, profile_id, name, sitting_height_cm, standing_height_cm, led_color, "
    "led_brightness, led_schedule, tilt_level, description"
)
ACTIVITY_MODE_UPDATE_COLUMNS = {
    "name": "name",
    "sitting_height_cm": "sitting_height_cm",
    "standing_height_cm": "standing_height_cm",
    "led_color": "led_color",
    "led_brightness": "led_brightness",
    "led_schedule": "led_schedule",
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
                _effective_mode_from_row(row, profile) for row in rows
            ]

        return await self._database.read(list_rows)

    async def create_mode(
        self, profile_id: str, create: ActivityModeCreate
    ) -> EffectiveActivityMode:
        mode_id = self._id_factory()
        normalized_name = normalize_activity_mode_name(create.name)

        def insert(connection: Connection) -> ActivityMode:
            profile = _get_profile_or_raise(connection, profile_id)
            try:
                connection.execute(
                    "INSERT INTO profile_modes "
                    "(id, profile_id, name, normalized_name, sitting_height_cm, "
                    "standing_height_cm, led_color, led_brightness, led_schedule, "
                    "tilt_level, description) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        mode_id,
                        profile_id,
                        create.name,
                        normalized_name,
                        profile.sitting_height_cm,
                        profile.standing_height_cm,
                        create.led_color,
                        create.led_brightness,
                        encode_schedule(create.led_schedule),
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
            return effective_mode_from_activity(
                _get_mode_or_raise(connection, mode_id), profile
            )

        return await self._database.write(insert)

    async def update_mode(
        self, mode_id: str, update: ActivityModeUpdate
    ) -> EffectiveActivityMode:
        changes = update.model_dump(exclude_unset=True, by_alias=False)
        # 높이는 프로필이 소유한다. 예전 client가 보내와도 모드에 쓰지 않는다.
        changes.pop("sitting_height_cm", None)
        changes.pop("standing_height_cm", None)
        if "name" in changes:
            changes["normalized_name"] = normalize_activity_mode_name(changes["name"])
        # 색이나 밝기를 직접 고르면 그 값으로 고정한다. 스케줄을 남겨 두면
        # 다음 구간에서 곧바로 덮어써, 고른 값이 잠깐만 보이고 사라진다.
        if ("led_schedule" not in changes
                and ("led_color" in changes or "led_brightness" in changes)):
            changes["led_schedule"] = None

        def update_row(connection: Connection) -> ActivityMode:
            _get_mode_or_raise(connection, mode_id)
            assignments: list[str] = []
            values: list[object] = []
            for field_name, column_name in ACTIVITY_MODE_UPDATE_COLUMNS.items():
                if field_name in changes:
                    assignments.append(f"{column_name} = ?")
                    value = changes[field_name]
                    values.append(
                        encode_schedule(value) if field_name == "led_schedule" else value
                    )
            if "normalized_name" in changes:
                assignments.append("normalized_name = ?")
                values.append(changes["normalized_name"])
            # 높이만 온 요청은 버린 뒤 바꿀 것이 남지 않는다. 빈 SET을 만들지 않고
            # 지금 상태를 그대로 돌려준다.
            if not assignments:
                mode = _get_mode_or_raise(connection, mode_id)
                return effective_mode_from_activity(
                    mode, _get_profile_or_raise(connection, mode.profile_id)
                )
            try:
                connection.execute(
                    f"UPDATE profile_modes SET {', '.join(assignments)} WHERE id = ?",
                    [*values, mode_id],
                )
            except sqlite3.IntegrityError as error:
                if _is_mode_name_conflict(error):
                    raise ActivityModeConflictError("같은 프로필에 이미 같은 작업 모드 이름이 있습니다.") from error
                raise
            mode = _get_mode_or_raise(connection, mode_id)
            return effective_mode_from_activity(
                mode, _get_profile_or_raise(connection, mode.profile_id)
            )

        return await self._database.write(update_row)

    async def delete_mode(self, mode_id: str) -> None:
        def delete_row(connection: Connection) -> None:
            cursor = connection.execute("DELETE FROM profile_modes WHERE id = ?", (mode_id,))
            if cursor.rowcount == 0:
                raise ActivityModeNotFoundError("요청한 작업 모드를 찾을 수 없습니다.")

        await self._database.write(delete_row)

    async def get_mode_for_profile(
        self, profile_id: str, mode_id: str
    ) -> tuple[ActivityMode, Profile]:
        """후속 자동화가 custom mode의 profile 소유권을 확인할 때 사용한다.

        높이는 프로필이 소유하므로, 공개 표현을 합성할 수 있도록 소유 프로필도
        함께 돌려준다.
        """

        def get_row(connection: Connection) -> tuple[ActivityMode, Profile]:
            profile = _get_profile_or_raise(connection, profile_id)
            mode = _get_mode_or_raise(connection, mode_id)
            if mode.profile_id != profile_id:
                raise ActivityModeOwnershipError("요청한 프로필의 작업 모드가 아닙니다.")
            return mode, profile

        return await self._database.read(get_row)


def _get_profile_or_raise(connection: Connection, profile_id: str) -> Profile:
    row = connection.execute(
        "SELECT id, name, sitting_height_cm, standing_height_cm, led_color, "
        "led_brightness, led_schedule, tilt_level, description FROM profiles WHERE id = ?",
        (profile_id,),
    ).fetchone()
    if row is None:
        raise ProfileNotFoundError("요청한 프로필을 찾을 수 없습니다.")
    return Profile.model_validate(_with_schedule(row))


def _get_mode_or_raise(connection: Connection, mode_id: str) -> ActivityMode:
    row = connection.execute(
        f"SELECT {ACTIVITY_MODE_SELECT_COLUMNS} FROM profile_modes WHERE id = ?",
        (mode_id,),
    ).fetchone()
    if row is None:
        raise ActivityModeNotFoundError("요청한 작업 모드를 찾을 수 없습니다.")
    return _activity_mode_from_row(row)


def _with_schedule(row: Row) -> dict:
    """행의 led_schedule은 JSON 문자열이다. 모델이 쓰는 형태로 풀어 준다."""

    values = dict(row)
    if "led_schedule" in values:
        values["led_schedule"] = decode_schedule(values["led_schedule"])
    return values


def _activity_mode_from_row(row: Row) -> ActivityMode:
    return ActivityMode.model_validate(_with_schedule(row))


def _default_mode_from_profile(profile: Profile) -> EffectiveActivityMode:
    return EffectiveActivityMode(
        key="default",
        kind="DEFAULT",
        name="기본",
        sitting_height_cm=profile.sitting_height_cm,
        standing_height_cm=profile.standing_height_cm,
        led_color=profile.led_color,
        led_brightness=profile.led_brightness,
        led_schedule=profile.led_schedule,
        tilt_level=profile.tilt_level,
        description=profile.description,
        editable=False,
    )


def _effective_mode_from_row(row: Row, profile: Profile) -> EffectiveActivityMode:
    return effective_mode_from_activity(_activity_mode_from_row(row), profile)


def effective_mode_from_activity(
    mode: ActivityMode, profile: Profile
) -> EffectiveActivityMode:
    """저장 custom row를 공개 합성 표현으로 바꾼다.

    높이는 프로필이 소유하므로 모드 row에 남아 있는 예전 값 대신 프로필의
    앉기·서기 높이를 싣는다. 모드는 LED와 틸트만 정한다.
    """

    return EffectiveActivityMode(
        key=mode.id,
        kind="CUSTOM",
        name=mode.name,
        sitting_height_cm=profile.sitting_height_cm,
        standing_height_cm=profile.standing_height_cm,
        led_color=mode.led_color,
        led_brightness=mode.led_brightness,
        led_schedule=mode.led_schedule,
        tilt_level=mode.tilt_level,
        description=mode.description,
        editable=True,
    )


def _is_mode_id_conflict(error: sqlite3.IntegrityError) -> bool:
    return getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY


def _is_mode_name_conflict(error: sqlite3.IntegrityError) -> bool:
    return getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE
