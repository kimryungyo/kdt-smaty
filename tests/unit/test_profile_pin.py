"""프로필 잠금 PIN의 해시 형식과 검증 경계를 확인한다."""

from pathlib import Path

import pytest

from smart_desk.modules.profiles import ProfileCreate, ProfileRepository
from smart_desk.modules.profiles.pin import (
    InvalidPinFormatError,
    hash_pin,
    validate_pin,
    verify_pin,
)
from smart_desk.storage import SQLiteDatabase


def test_hash_is_salted_and_never_contains_the_pin() -> None:
    first = hash_pin("1234", iterations=1)
    second = hash_pin("1234", iterations=1)

    assert first != second, "같은 PIN이라도 salt가 달라 해시가 달라야 한다."
    assert "1234" not in first
    assert first.startswith("pbkdf2_sha256$")
    assert verify_pin("1234", first)
    assert verify_pin("1234", second)


def test_wrong_pin_and_broken_hash_are_rejected() -> None:
    stored = hash_pin("1234", iterations=1)

    assert not verify_pin("1235", stored)
    assert not verify_pin("1234", None)
    assert not verify_pin("1234", "plaintext")
    assert not verify_pin("1234", "sha1$1$00$00")
    assert not verify_pin("12345", stored)


@pytest.mark.parametrize("pin", ["123", "12345", "12a4", "", " 1234", "１２３４"])
def test_invalid_pin_format_is_rejected(pin: str) -> None:
    with pytest.raises(InvalidPinFormatError):
        validate_pin(pin)
    with pytest.raises(InvalidPinFormatError):
        hash_pin(pin)


async def test_pin_round_trips_through_repository_and_is_not_exposed(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "smart-desk.db")
    await database.start()
    repository = ProfileRepository(database)
    profile = await repository.create_profile(
        ProfileCreate(name="사용자", sittingHeightCm=80, standingHeightCm=105)
    )

    assert profile.has_pin is False
    assert await repository.get_pin_hash(profile.id) is None

    await repository.set_pin_hash(profile.id, hash_pin("4321", iterations=1))
    stored = await repository.get_pin_hash(profile.id)

    assert stored is not None
    assert verify_pin("4321", stored)
    reloaded = await repository.get_profile(profile.id)
    assert reloaded.has_pin is True
    assert "pin" not in reloaded.model_dump(by_alias=False) or reloaded.model_dump()["hasPin"] is True
    assert stored not in str(reloaded.model_dump())

    await repository.set_pin_hash(profile.id, None)
    assert (await repository.get_profile(profile.id)).has_pin is False
    await database.stop()
