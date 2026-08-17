"""작업 모드 repository의 합성, 중복과 소유권 경계를 검증한다."""

from pathlib import Path

import pytest

from smart_desk.modules.profiles import (
    ActivityModeConflictError,
    ActivityModeCreate,
    ActivityModeOwnershipError,
    ActivityModeRepository,
    ActivityModeUpdate,
    ProfileCreate,
    ProfileRepository,
)
from smart_desk.storage import SQLiteDatabase


async def test_default_is_synthesized_and_custom_modes_are_profile_scoped(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "smart-desk.db")
    await database.start()
    profile_ids = iter(["profile-" + "a" * 32, "profile-" + "b" * 32])
    profiles = ProfileRepository(database, id_factory=lambda: next(profile_ids))
    modes = ActivityModeRepository(database, id_factory=lambda: "mode-" + "c" * 32)
    first = await profiles.create_profile(ProfileCreate(name="A", sittingHeightCm=80, standingHeightCm=105, ledColor="FF0000"))
    second = await profiles.create_profile(ProfileCreate(name="B", sittingHeightCm=81, standingHeightCm=106))

    created = await modes.create_mode(
        first.id,
        ActivityModeCreate(name=" 독서 ", sittingHeightCm=82, standingHeightCm=108, ledColor="ffd080"),
    )
    effective = await modes.list_effective_modes(first.id)

    assert [item.model_dump() for item in effective] == [
        {"key": "default", "kind": "DEFAULT", "name": "기본", "sittingHeightCm": 80.0, "standingHeightCm": 105.0, "ledColor": "FF0000", "editable": False},
        {"key": created.id, "kind": "CUSTOM", "name": "독서", "sittingHeightCm": 82.0, "standingHeightCm": 108.0, "ledColor": "FFD080", "editable": True},
    ]
    with pytest.raises(ActivityModeOwnershipError):
        await modes.get_mode_for_profile(second.id, created.id)
    await database.stop()


async def test_normalized_duplicate_is_rejected_and_profile_delete_cascades(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "smart-desk.db")
    await database.start()
    profile = await ProfileRepository(database).create_profile(
        ProfileCreate(name="A", sittingHeightCm=80, standingHeightCm=105)
    )
    modes = ActivityModeRepository(database)
    mode = await modes.create_mode(
        profile.id, ActivityModeCreate(name="Reading", sittingHeightCm=82, standingHeightCm=108)
    )
    with pytest.raises(ActivityModeConflictError):
        await modes.create_mode(
            profile.id, ActivityModeCreate(name=" reading ", sittingHeightCm=83, standingHeightCm=109)
        )
    other = await modes.create_mode(
        profile.id, ActivityModeCreate(name="Study", sittingHeightCm=83, standingHeightCm=109)
    )
    with pytest.raises(ActivityModeConflictError):
        await modes.update_mode(other.id, ActivityModeUpdate(name=" READING "))

    await ProfileRepository(database).delete_profile(profile.id)
    assert await database.read(
        lambda connection: connection.execute("SELECT COUNT(*) FROM profile_modes").fetchone()[0]
    ) == 0
    await database.stop()
