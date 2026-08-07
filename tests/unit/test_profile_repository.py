"""임시 SQLite 파일을 사용하는 ProfileRepository CRUD 테스트."""

import asyncio
from pathlib import Path

import pytest

from smart_desk.modules.profiles import (
    Profile,
    ProfileConflictError,
    ProfileCreate,
    ProfileNotFoundError,
    ProfileRepository,
    ProfileUpdate,
)
from smart_desk.storage import SQLiteDatabase


def create_input(name: str = "사용자", led_color: str | None = "aa00ff") -> ProfileCreate:
    return ProfileCreate(
        name=name,
        sittingHeightCm=80,
        standingHeightCm=105,
        ledColor=led_color,
    )


async def test_profile_repository_crud_and_deterministic_listing(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "smart-desk.db")
    await database.start()
    identifiers = iter(["profile-" + "2" * 32, "profile-" + "1" * 32])
    repository = ProfileRepository(database, id_factory=lambda: next(identifiers))

    assert await repository.list_profiles() == []
    first = await repository.create_profile(create_input("beta"))
    second = await repository.create_profile(create_input("Alpha", None))

    assert isinstance(first, Profile)
    assert first.id == "profile-" + "2" * 32
    assert first.led_color == "AA00FF"
    assert await repository.get_profile(second.id) == second
    assert [profile.name for profile in await repository.list_profiles()] == [
        "Alpha",
        "beta",
    ]

    updated = await repository.update_profile(
        first.id,
        ProfileUpdate(name=" new name ", ledColor=None),
    )
    assert updated.name == "new name"
    assert updated.sitting_height_cm == 80.0
    assert updated.standing_height_cm == 105.0
    assert updated.led_color is None

    await repository.delete_profile(second.id)
    with pytest.raises(ProfileNotFoundError):
        await repository.get_profile(second.id)
    await database.stop()


async def test_missing_profile_operations_preserve_domain_error(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "smart-desk.db")
    await database.start()
    repository = ProfileRepository(database)

    with pytest.raises(ProfileNotFoundError):
        await repository.get_profile("missing")
    with pytest.raises(ProfileNotFoundError):
        await repository.update_profile("missing", ProfileUpdate(name="new"))
    with pytest.raises(ProfileNotFoundError):
        await repository.delete_profile("missing")
    await database.stop()


async def test_generated_id_collision_does_not_replace_existing_profile(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "smart-desk.db")
    await database.start()
    profile_id = "profile-" + "a" * 32
    repository = ProfileRepository(database, id_factory=lambda: profile_id)
    original = await repository.create_profile(create_input("original"))

    with pytest.raises(ProfileConflictError):
        await repository.create_profile(create_input("replacement"))

    assert await repository.get_profile(profile_id) == original
    await database.stop()


async def test_profiles_persist_across_database_and_repository_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "smart-desk.db"
    first_database = SQLiteDatabase(path)
    await first_database.start()
    created = await ProfileRepository(first_database).create_profile(create_input())
    await first_database.stop()

    second_database = SQLiteDatabase(path)
    await second_database.start()
    loaded = await ProfileRepository(second_database).get_profile(created.id)

    assert loaded == created
    await second_database.stop()


async def test_concurrent_partial_updates_do_not_lose_other_fields(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "smart-desk.db")
    await database.start()
    repository = ProfileRepository(database)
    created = await repository.create_profile(create_input())

    await asyncio.gather(
        repository.update_profile(created.id, ProfileUpdate(name="changed")),
        repository.update_profile(
            created.id,
            ProfileUpdate(standingHeightCm=110),
        ),
    )

    updated = await repository.get_profile(created.id)
    assert updated.name == "changed"
    assert updated.standing_height_cm == 110.0
    assert updated.sitting_height_cm == 80.0
    await database.stop()
