"""대시보드 HTTP 경계에서 사용하는 요청·응답 모델이다."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from smart_desk.modules.desk.models import (
    DeskSnapshot,
    DeskState,
    Direction,
    HeightProvenance,
    HeightStatus,
    RelayEvent,
    RelayState,
)


def _to_camel(field_name: str) -> str:
    first, *rest = field_name.split("_")
    return first + "".join(part.capitalize() for part in rest)


class DashboardModel(BaseModel):
    """Python snake_case와 HTTP camelCase를 일관되게 변환한다."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        serialize_by_alias=True,
        validate_by_alias=True,
        validate_by_name=True,
    )


class HeightResponse(DashboardModel):
    height_cm: float | None
    observed_at: datetime | None
    status: HeightStatus
    provenance: HeightProvenance | None


class RelayResponse(DashboardModel):
    event: RelayEvent | None
    state: RelayState | None
    firmware: str | None
    code: str | None
    detail: str | None
    received_at: datetime | None
    last_error: str | None


class DashboardStatusResponse(DashboardModel):
    state: DeskState
    height: HeightResponse
    relay: RelayResponse
    target_height_cm: float | None
    direction: Direction | None
    detail: str
    last_error: str | None
    updated_at: datetime

    @classmethod
    def from_snapshot(cls, snapshot: DeskSnapshot) -> DashboardStatusResponse:
        """불변 Desk snapshot을 명시적인 HTTP 응답으로 변환한다."""

        return cls(
            state=snapshot.state,
            height=HeightResponse(
                height_cm=snapshot.height.height_cm,
                observed_at=snapshot.height.observed_at,
                status=snapshot.height.status,
                provenance=snapshot.height.provenance,
            ),
            relay=RelayResponse(
                event=snapshot.relay.event,
                state=snapshot.relay.state,
                firmware=snapshot.relay.firmware,
                code=snapshot.relay.code,
                detail=snapshot.relay.detail,
                received_at=snapshot.relay.received_at,
                last_error=snapshot.relay.last_error,
            ),
            target_height_cm=snapshot.target_height_cm,
            direction=snapshot.direction,
            detail=snapshot.detail,
            last_error=snapshot.last_error,
            updated_at=snapshot.updated_at,
        )


class HoldControlRequest(DashboardModel):
    action: Literal["HOLD"]
    direction: Direction


class StopControlRequest(DashboardModel):
    action: Literal["STOP"]


ControlRequest = Annotated[
    HoldControlRequest | StopControlRequest,
    Field(discriminator="action"),
]


class SetTargetRequest(DashboardModel):
    action: Literal["SET"]
    target_cm: float = Field(strict=True, ge=75, le=115, allow_inf_nan=False)


class CancelTargetRequest(DashboardModel):
    action: Literal["CANCEL"]


TargetRequest = Annotated[
    SetTargetRequest | CancelTargetRequest,
    Field(discriminator="action"),
]
