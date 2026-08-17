"""Voice lifecycle snapshot API."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
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
