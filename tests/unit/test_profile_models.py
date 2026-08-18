"""프로필 Pydantic alias와 값 검증 테스트."""

import math

import pytest
from pydantic import ValidationError

from smart_desk.modules.profiles import (
    ActivityModeCreate,
    ActivityModeUpdate,
    EffectiveActivityMode,
    Profile,
    ProfileCreate,
    ProfileUpdate,
)


def test_profile_accepts_snake_and_camel_case_and_serializes_aliases() -> None:
    profile = Profile(
        id="profile-" + "a" * 32,
        name="  홍길동  ",
        sittingHeightCm=80,
        standing_height_cm=105.0,
        ledColor="ff30a0",
        ledBrightness=200,
        tiltLevel=None,
        description=None,
    )

    assert profile.name == "홍길동"
    assert profile.led_color == "FF30A0"
    assert profile.model_dump() == {
        "id": "profile-" + "a" * 32,
        "name": "홍길동",
        "sittingHeightCm": 80.0,
        "standingHeightCm": 105.0,
        "ledColor": "FF30A0",
        "ledBrightness": 200,
        "ledSchedule": None,
        "hasPin": False,
        "tiltLevel": None,
        "description": None,
        "hasPin": False,
    }


@pytest.mark.parametrize("height", [75, 75.0, 115, 115.0])
def test_profile_height_boundaries_are_inclusive(height: float) -> None:
    create = ProfileCreate(name="test", sittingHeightCm=height, standingHeightCm=height)

    assert create.sitting_height_cm == float(height)


@pytest.mark.parametrize(
    "height",
    [74.99, 115.01, True, False, "80", math.nan, math.inf, -math.inf],
)
def test_invalid_profile_heights_are_rejected(height: object) -> None:
    with pytest.raises(ValidationError):
        ProfileCreate(name="test", sittingHeightCm=height, standingHeightCm=100)


@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
def test_blank_profile_name_is_rejected(name: str) -> None:
    with pytest.raises(ValidationError):
        ProfileCreate(name=name, sittingHeightCm=80, standingHeightCm=100)


@pytest.mark.parametrize("led_color", ["12345", "GG0000", "#FF0000", 123456])
def test_invalid_led_color_is_rejected(led_color: object) -> None:
    with pytest.raises(ValidationError):
        ProfileCreate(
            name="test",
            sittingHeightCm=80,
            standingHeightCm=100,
            ledColor=led_color,
        )


@pytest.mark.parametrize("tilt_level", [0, 10])
def test_tilt_level_boundaries_are_inclusive(tilt_level: int) -> None:
    create = ProfileCreate(
        name="test", sittingHeightCm=80, standingHeightCm=100, tiltLevel=tilt_level
    )

    assert create.tilt_level == tilt_level


@pytest.mark.parametrize("tilt_level", [-1, 11, 1.5, True, "1"])
def test_invalid_tilt_level_is_rejected(tilt_level: object) -> None:
    with pytest.raises(ValidationError):
        ProfileCreate(
            name="test", sittingHeightCm=80, standingHeightCm=100, tiltLevel=tilt_level
        )


def test_description_is_trimmed_and_blank_becomes_none() -> None:
    create = ProfileCreate(
        name="test", sittingHeightCm=80, standingHeightCm=100, description="  집중 모드  "
    )

    assert create.description == "집중 모드"

    blank = ProfileCreate(
        name="test", sittingHeightCm=80, standingHeightCm=100, description="   "
    )

    assert blank.description is None


def test_description_over_max_length_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ProfileCreate(
            name="test", sittingHeightCm=80, standingHeightCm=100, description="x" * 301
        )


def test_create_rejects_unknown_fields_and_client_id() -> None:
    with pytest.raises(ValidationError):
        ProfileCreate.model_validate(
            {
                "id": "profile-" + "a" * 32,
                "name": "test",
                "sittingHeightCm": 80,
                "standingHeightCm": 100,
            }
        )


def test_update_distinguishes_empty_unset_and_nullable_led() -> None:
    with pytest.raises(ValidationError):
        ProfileUpdate()
    with pytest.raises(ValidationError):
        ProfileUpdate(name=None)
    with pytest.raises(ValidationError):
        ProfileUpdate(sittingHeightCm=None)
    with pytest.raises(ValidationError):
        ProfileUpdate(standingHeightCm=None)

    update = ProfileUpdate(ledColor=None)
    assert update.model_fields_set == {"led_color"}
    assert update.model_dump(exclude_unset=True) == {"ledColor": None}

    mode_update = ProfileUpdate(tiltLevel=None, description=None)
    assert mode_update.model_fields_set == {"tilt_level", "description"}
    assert mode_update.model_dump(exclude_unset=True) == {
        "tiltLevel": None, "description": None,
    }


def test_activity_mode_models_normalize_and_expose_effective_contract() -> None:
    create = ActivityModeCreate(
        name=" 독서 ", sittingHeightCm=82, standingHeightCm=108, ledColor="ffd080"
    )
    effective = EffectiveActivityMode(
        key="default",
        kind="DEFAULT",
        name="기본",
        sittingHeightCm=80,
        standingHeightCm=105,
        ledColor=None,
        ledBrightness=None,
        tiltLevel=None,
        description=None,
        editable=False,
    )

    assert create.name == "독서"
    assert create.led_color == "FFD080"
    assert effective.model_dump() == {
        "key": "default", "kind": "DEFAULT", "name": "기본",
        "sittingHeightCm": 80.0, "standingHeightCm": 105.0,
        "ledColor": None, "ledBrightness": None, "ledSchedule": None, "tiltLevel": None,
        "description": None, "editable": False,
    }


def test_activity_mode_update_rejects_empty_unknown_or_nullable_required_fields() -> None:
    with pytest.raises(ValidationError):
        ActivityModeUpdate()
    with pytest.raises(ValidationError):
        ActivityModeUpdate(name=None)
    with pytest.raises(ValidationError):
        ActivityModeCreate.model_validate(
            {"name": "독서", "sittingHeightCm": 82, "standingHeightCm": 108, "unknown": True}
        )

    update = ActivityModeUpdate(ledColor=None)
    assert update.model_dump(exclude_unset=True) == {"ledColor": None}
