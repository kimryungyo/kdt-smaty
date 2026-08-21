"""프로필 저장과 외부 표현에 사용하는 Pydantic 모델이다."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PROFILE_ID_PATTERN = r"^profile-[0-9a-f]{32}$"
ACTIVITY_MODE_ID_PATTERN = r"^mode-[0-9a-f]{32}$"
LED_COLOR_PATTERN = re.compile(r"^[0-9A-Fa-f]{6}$")

# tilt_level의 0~10 범위는 저장소 수준의 안전 범위이며, 실제 장치 한계
# (TiltSettings.min_level/max_level)를 대체하지 않는다. 실제 장치 범위 검증은
# tilt 자동화가 연결된 뒤 API/자동화 계층에서 수행한다.
TILT_LEVEL_MIN = 0
TILT_LEVEL_MAX = 10
# WLED가 그대로 받는 밝기 범위. None은 "이 모드는 밝기를 건드리지 않는다"는 뜻이다.
LED_BRIGHTNESS_MIN = 0
LED_BRIGHTNESS_MAX = 255
DESCRIPTION_MAX_LENGTH = 300


def _to_camel(field_name: str) -> str:
    first, *rest = field_name.split("_")
    return first + "".join(part.capitalize() for part in rest)


class _ProfileModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        serialize_by_alias=True,
        validate_by_alias=True,
        validate_by_name=True,
    )

    @field_validator("name", check_fields=False)
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("프로필 이름은 비어 있을 수 없습니다.")
        return normalized

    @field_validator("led_color", check_fields=False)
    @classmethod
    def normalize_led_color(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if LED_COLOR_PATTERN.fullmatch(value) is None:
            raise ValueError("LED 색상은 6자리 hexadecimal 문자열이어야 합니다.")
        return value.upper()

    @field_validator("led_schedule", check_fields=False)
    @classmethod
    def normalize_led_schedule(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        """구간이 올바른지 확인하고 정렬된 표준 형태로 되돌린다."""

        if value is None:
            return None
        from smart_desk.modules.profiles.led_schedule import parse_schedule, schedule_to_raw

        try:
            return schedule_to_raw(parse_schedule(value))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"조명 스케줄이 올바르지 않습니다: {error}") from error

    @field_validator("description", check_fields=False)
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ProfilePin(_ProfileModel):
    """프로필 잠금 PIN 입력이다. 서버는 해시만 저장한다."""

    pin: str = Field(strict=True, pattern=r"^[0-9]{4}$")


class Profile(_ProfileModel):
    """저장되어 server ID가 부여된 프로필이다."""

    id: str = Field(strict=True, pattern=PROFILE_ID_PATTERN)
    name: str = Field(strict=True)
    sitting_height_cm: float = Field(
        strict=True,
        ge=75,
        le=115,
        allow_inf_nan=False,
    )
    standing_height_cm: float = Field(
        strict=True,
        ge=75,
        le=115,
        allow_inf_nan=False,
    )
    led_color: str | None = Field(strict=True)
    led_brightness: int | None = Field(
        strict=True, ge=LED_BRIGHTNESS_MIN, le=LED_BRIGHTNESS_MAX
    )
    led_schedule: dict[str, Any] | None = Field(default=None)
    has_pin: bool = Field(default=False, strict=True)
    tilt_level: int | None = Field(strict=True, ge=TILT_LEVEL_MIN, le=TILT_LEVEL_MAX)
    description: str | None = Field(strict=True, max_length=DESCRIPTION_MAX_LENGTH)
    has_pin: bool = Field(default=False, strict=True)


class ProfileCreate(_ProfileModel):
    """새 프로필 생성에 필요한 사용자 입력이다."""

    name: str = Field(strict=True)
    sitting_height_cm: float = Field(
        strict=True,
        ge=75,
        le=115,
        allow_inf_nan=False,
    )
    standing_height_cm: float = Field(
        strict=True,
        ge=75,
        le=115,
        allow_inf_nan=False,
    )
    led_color: str | None = Field(default=None, strict=True)
    led_brightness: int | None = Field(
        default=None, strict=True, ge=LED_BRIGHTNESS_MIN, le=LED_BRIGHTNESS_MAX
    )
    led_schedule: dict[str, Any] | None = Field(default=None)
    tilt_level: int | None = Field(default=None, strict=True, ge=TILT_LEVEL_MIN, le=TILT_LEVEL_MAX)
    description: str | None = Field(default=None, strict=True, max_length=DESCRIPTION_MAX_LENGTH)


class ProfileUpdate(_ProfileModel):
    """명시적으로 전달된 프로필 필드만 변경하는 입력이다."""

    name: str | None = Field(default=None, strict=True)
    sitting_height_cm: float | None = Field(
        default=None,
        strict=True,
        ge=75,
        le=115,
        allow_inf_nan=False,
    )
    standing_height_cm: float | None = Field(
        default=None,
        strict=True,
        ge=75,
        le=115,
        allow_inf_nan=False,
    )
    led_color: str | None = Field(default=None, strict=True)
    led_brightness: int | None = Field(
        default=None, strict=True, ge=LED_BRIGHTNESS_MIN, le=LED_BRIGHTNESS_MAX
    )
    led_schedule: dict[str, Any] | None = Field(default=None)
    tilt_level: int | None = Field(default=None, strict=True, ge=TILT_LEVEL_MIN, le=TILT_LEVEL_MAX)
    description: str | None = Field(default=None, strict=True, max_length=DESCRIPTION_MAX_LENGTH)

    @model_validator(mode="after")
    def require_valid_changes(self) -> ProfileUpdate:
        if not self.model_fields_set:
            raise ValueError("변경할 프로필 필드를 하나 이상 전달해야 합니다.")

        nullable_fields = {"name", "sitting_height_cm", "standing_height_cm"}
        for field_name in self.model_fields_set & nullable_fields:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} 필드는 null일 수 없습니다.")
        return self


class _ActivityModeModel(_ProfileModel):
    @field_validator("name", check_fields=False)
    @classmethod
    def normalize_activity_mode_name(cls, value: str | None) -> str | None:
        return cls.normalize_name(value)


class ActivityModeCreate(_ActivityModeModel):
    """사용자 정의 작업 모드 생성 입력이다."""

    name: str = Field(strict=True)
    # 높이는 프로필이 소유한다. 예전 client가 보내오면 받아만 두고, 저장할 때
    # repository가 프로필 높이로 덮어쓴다.
    sitting_height_cm: float | None = Field(
        default=None, strict=True, ge=75, le=115, allow_inf_nan=False
    )
    standing_height_cm: float | None = Field(
        default=None, strict=True, ge=75, le=115, allow_inf_nan=False
    )
    led_color: str | None = Field(default=None, strict=True)
    led_brightness: int | None = Field(
        default=None, strict=True, ge=LED_BRIGHTNESS_MIN, le=LED_BRIGHTNESS_MAX
    )
    led_schedule: dict[str, Any] | None = Field(default=None)
    tilt_level: int | None = Field(default=None, strict=True, ge=TILT_LEVEL_MIN, le=TILT_LEVEL_MAX)
    description: str | None = Field(default=None, strict=True, max_length=DESCRIPTION_MAX_LENGTH)


class ActivityModeUpdate(_ActivityModeModel):
    """사용자 정의 작업 모드의 명시적 부분 수정 입력이다."""

    name: str | None = Field(default=None, strict=True)
    sitting_height_cm: float | None = Field(
        default=None, strict=True, ge=75, le=115, allow_inf_nan=False
    )
    standing_height_cm: float | None = Field(
        default=None, strict=True, ge=75, le=115, allow_inf_nan=False
    )
    led_color: str | None = Field(default=None, strict=True)
    led_brightness: int | None = Field(
        default=None, strict=True, ge=LED_BRIGHTNESS_MIN, le=LED_BRIGHTNESS_MAX
    )
    led_schedule: dict[str, Any] | None = Field(default=None)
    tilt_level: int | None = Field(default=None, strict=True, ge=TILT_LEVEL_MIN, le=TILT_LEVEL_MAX)
    description: str | None = Field(default=None, strict=True, max_length=DESCRIPTION_MAX_LENGTH)

    @model_validator(mode="after")
    def require_valid_changes(self) -> ActivityModeUpdate:
        if not self.model_fields_set:
            raise ValueError("변경할 작업 모드 필드를 하나 이상 전달해야 합니다.")
        for field_name in self.model_fields_set & {"name"}:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} 필드는 null일 수 없습니다.")
        return self


class ActivityMode(_ActivityModeModel):
    """저장된 사용자 정의 작업 모드다."""

    id: str = Field(strict=True, pattern=ACTIVITY_MODE_ID_PATTERN)
    profile_id: str = Field(strict=True, pattern=PROFILE_ID_PATTERN)
    name: str = Field(strict=True)
    sitting_height_cm: float = Field(strict=True, ge=75, le=115, allow_inf_nan=False)
    standing_height_cm: float = Field(strict=True, ge=75, le=115, allow_inf_nan=False)
    led_color: str | None = Field(strict=True)
    led_brightness: int | None = Field(
        strict=True, ge=LED_BRIGHTNESS_MIN, le=LED_BRIGHTNESS_MAX
    )
    led_schedule: dict[str, Any] | None = Field(default=None)
    tilt_level: int | None = Field(strict=True, ge=TILT_LEVEL_MIN, le=TILT_LEVEL_MAX)
    description: str | None = Field(strict=True, max_length=DESCRIPTION_MAX_LENGTH)


class EffectiveActivityMode(_ActivityModeModel):
    """기본 profile 값과 custom row를 합성한 설정 화면용 작업 모드다."""

    key: str = Field(strict=True)
    kind: str = Field(strict=True, pattern=r"^(DEFAULT|CUSTOM)$")
    name: str = Field(strict=True)
    sitting_height_cm: float = Field(strict=True, ge=75, le=115, allow_inf_nan=False)
    standing_height_cm: float = Field(strict=True, ge=75, le=115, allow_inf_nan=False)
    led_color: str | None = Field(strict=True)
    led_brightness: int | None = Field(
        strict=True, ge=LED_BRIGHTNESS_MIN, le=LED_BRIGHTNESS_MAX
    )
    led_schedule: dict[str, Any] | None = Field(default=None)
    tilt_level: int | None = Field(strict=True, ge=TILT_LEVEL_MIN, le=TILT_LEVEL_MAX)
    description: str | None = Field(strict=True, max_length=DESCRIPTION_MAX_LENGTH)
    editable: bool = Field(strict=True)
