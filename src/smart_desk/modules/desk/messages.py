"""책상 높이와 ESP32 릴레이 MQTT JSON 계약."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)

from smart_desk.config.constants import DESK_PHYSICAL_MAX_CM, DESK_PHYSICAL_MIN_CM
from smart_desk.modules.desk.models import Direction, RelayEvent, RelayState


class HeightMessage(BaseModel):
    """유효한 실제 높이 관측을 발행하는 wire model."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        serialize_by_alias=True,
    )

    schema_name: Literal["smartdesk.height.v1"] = Field(
        default="smartdesk.height.v1",
        alias="schema",
    )
    observed_at: datetime
    height_cm: float = Field(allow_inf_nan=False)

    @field_validator("observed_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        """관측 시각은 모호하지 않은 UTC 시각만 허용한다."""

        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("높이 관측 시각은 timezone-aware UTC여야 합니다.")
        return value


class RelayPulseMessage(BaseModel):
    """ESP32에 전달하는 UP 또는 DOWN deadline 연장 명령."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command: Direction
    source: Literal["desk_service"] = "desk_service"
    hold_ms: int = Field(strict=True, ge=50, le=500)


class RelayWakeMessage(BaseModel):
    """절전 높이 센서를 깨우는 단 한 번의 짧은 firmware 명령."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command: Literal["WAKE"] = "WAKE"
    source: Literal["desk_service"] = "desk_service"
    direction: Direction
    hold_ms: int = Field(default=400, strict=True)
    basis_height_cm: float = Field(
        strict=True,
        allow_inf_nan=False,
        ge=DESK_PHYSICAL_MIN_CM,
        le=DESK_PHYSICAL_MAX_CM,
    )

    @field_validator("hold_ms")
    @classmethod
    def require_exact_wake_hold(cls, value: int) -> int:
        if value != 400:
            raise ValueError("WAKE hold_ms는 정확히 400ms여야 합니다.")
        return value


class RelayStopMessage(BaseModel):
    """ESP32에 전달하는 필드 하나의 STOP 명령."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command: Literal["STOP"] = "STOP"


class RelayStatusMessage(BaseModel):
    """ESP32 live 상태와 MQTT offline Will의 수신 wire model."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    event: RelayEvent
    state: RelayState
    firmware: StrictStr | None = None
    code: StrictStr | None = None
    detail: StrictStr | None = None

    @field_validator("firmware", "code", "detail")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        """제공된 진단 문자열은 공백을 제거하고 빈 값을 거부한다."""

        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("ESP32 상태 문자열은 비어 있을 수 없습니다.")
        return normalized

    @model_validator(mode="after")
    def require_live_firmware(self) -> RelayStatusMessage:
        """offline Will을 제외한 정상 펌웨어 발행에는 버전을 요구한다."""

        if self.event is not RelayEvent.OFFLINE and self.firmware is None:
            raise ValueError("ESP32 live 상태에는 firmware가 필요합니다.")
        return self
