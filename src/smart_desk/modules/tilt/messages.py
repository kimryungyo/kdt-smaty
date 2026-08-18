"""틸팅 MQTT 명령·상태 wire 계약."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from smart_desk.modules.tilt.models import TiltState


class TiltGotoCommand(BaseModel):
    """특정 단계로 이동을 요청하는 수신 wire model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: Literal["smartdesk.tilt.command.v1"] = Field(
        default="smartdesk.tilt.command.v1", alias="schema"
    )
    command: Literal["GOTO"] = "GOTO"
    level: int = Field(strict=True, ge=0)
    source: str | None = None


class TiltStopCommand(BaseModel):
    """진행 중인 이동을 취소하는 수신 wire model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: Literal["smartdesk.tilt.command.v1"] = Field(
        default="smartdesk.tilt.command.v1", alias="schema"
    )
    command: Literal["STOP"] = "STOP"


TiltCommand = Annotated[
    Union[TiltGotoCommand, TiltStopCommand],
    Field(discriminator="command"),
]

TiltCommandAdapter: TypeAdapter[TiltGotoCommand | TiltStopCommand] = TypeAdapter(TiltCommand)


class TiltStatusMessage(BaseModel):
    """틸팅 상태를 발행하는 wire model."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        serialize_by_alias=True,
        populate_by_name=True,
    )

    schema_name: Literal["smartdesk.tilt.status.v1"] = Field(
        default="smartdesk.tilt.status.v1", alias="schema"
    )
    state: TiltState
    level: int | None = None
    target_level: int | None = Field(default=None, alias="targetLevel")
    position_mm: float | None = Field(default=None, alias="positionMm")
    position_valid: bool = Field(default=False, alias="positionValid")
    firmware: str | None = None
    detail: str
    last_error: str | None = Field(default=None, alias="lastError")
    updated_at: datetime = Field(alias="updatedAt")

    @field_validator("updated_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        """상태 시각은 모호하지 않은 UTC 시각만 허용한다."""

        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("틸팅 상태 시각은 timezone-aware UTC여야 합니다.")
        return value
