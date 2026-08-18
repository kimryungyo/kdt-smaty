"""Voice lifecycle snapshot API."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from smart_desk.core.container import get_container
from smart_desk.modules.voice.models import VoiceSnapshot, VoiceState


def _camel(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(word.title() for word in rest)


class VoiceStatusResponse(BaseModel):
    """Content-free public projection of the Voice service state."""

    model_config = ConfigDict(
        alias_generator=_camel,
        extra="forbid",
        frozen=True,
        serialize_by_alias=True,
        validate_by_alias=True,
        validate_by_name=True,
    )

    state: VoiceState
    last_transition_at: datetime | None
    followup_expires_at: datetime | None
    last_error: str | None


def _response(snapshot: VoiceSnapshot) -> VoiceStatusResponse:
    return VoiceStatusResponse(
        state=snapshot.state,
        last_transition_at=snapshot.last_transition_at,
        followup_expires_at=snapshot.followup_expires_at,
        last_error=snapshot.last_error,
    )


router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.get("/status", response_model=VoiceStatusResponse)
async def status() -> VoiceStatusResponse:
    """Return Voice state without transcripts, audio, or provider details."""

    voice = get_container().voice
    if voice is None:
        return VoiceStatusResponse(
            state=VoiceState.DISABLED,
            last_transition_at=None,
            followup_expires_at=None,
            last_error=None,
        )
    return _response(voice.get_snapshot())


class GreetRequest(BaseModel):
    """어느 프로필에게 인사할지 지정한다."""

    model_config = ConfigDict(
        alias_generator=_camel, extra="forbid", frozen=True,
        serialize_by_alias=True, validate_by_alias=True, validate_by_name=True,
    )

    profile_id: str
    # 쉬는 시간을 무시하고 지금 바로 인사하게 한다. 확인용이다.
    force: bool = False


@router.post("/greet", status_code=202)
async def greet(request: GreetRequest) -> dict[str, str]:
    """인사를 지금 건네게 한다. 평소에는 얼굴을 알아본 순간 자동으로 불린다."""

    greeting = get_container().greeting
    if greeting is None:
        raise HTTPException(503, "음성 인사가 꺼져 있습니다.")
    if request.force:
        greeting.reset(request.profile_id)
    greeting.greet(request.profile_id)
    return {"status": "accepted"}
