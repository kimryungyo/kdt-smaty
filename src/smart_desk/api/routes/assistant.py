"""Latest current-session Assistant projection (polling only)."""
from __future__ import annotations
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from smart_desk.core.container import get_container
from smart_desk.modules.assistant.turns import AssistantTurn, TurnPhase, TurnStatus


def _camel(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(word.title() for word in rest)


class AssistantApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel,
        extra="forbid",
        frozen=True,
        serialize_by_alias=True,
        validate_by_alias=True,
        validate_by_name=True,
    )


class TurnResponse(AssistantApiModel):
    turn_id: str
    session_id: str | None
    profile_id: str | None
    phase: TurnPhase
    sequence: int
    status: TurnStatus
    title: str
    summary: str | None
    detail: str | None
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    error_code: str | None


class LatestResponse(AssistantApiModel):
    turn: TurnResponse | None


router = APIRouter(prefix="/api/assistant", tags=["assistant"])


@router.get("/latest", response_model=LatestResponse)
async def latest() -> LatestResponse:
    store = get_container().assistant_turns
    turn: AssistantTurn | None = await store.latest() if store is not None else None
    if turn is None:
        return LatestResponse(turn=None)
    return LatestResponse(
        turn=TurnResponse(
            turn_id=turn.turn_id,
            session_id=turn.session_id,
            profile_id=turn.profile_id,
            phase=turn.phase,
            sequence=turn.sequence,
            status=turn.status,
            title=turn.title,
            summary=turn.summary,
            detail=turn.detail,
            started_at=turn.started_at,
            updated_at=turn.updated_at,
            completed_at=turn.completed_at,
            error_code=turn.error_code,
        )
    )
