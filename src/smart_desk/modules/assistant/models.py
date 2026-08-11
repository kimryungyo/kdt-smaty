"""AI 응답 schema와 provider history DTO를 정의한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator


HistoryItem = dict[str, object]


class AssistantReply(BaseModel):
    """local speaker가 읽을 짧은 structured 응답이다."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    spoken_text: Annotated[str, StringConstraints(min_length=1, max_length=240)]

    @field_validator("spoken_text")
    @classmethod
    def require_single_paragraph(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("spoken_text는 한 문단이어야 합니다.")
        return value


@dataclass(frozen=True, slots=True)
class OpenAiTurn:
    """검증된 응답과 다음 요청에 재전달할 전체 output item이다."""

    reply: AssistantReply
    output_items: tuple[HistoryItem, ...]
    request_id: str | None
    input_tokens: int | None
    output_tokens: int | None

    def __post_init__(self) -> None:
        if any(not isinstance(item, dict) for item in self.output_items):
            raise ValueError("OpenAI output item은 JSON object여야 합니다.")
        for tokens in (self.input_tokens, self.output_tokens):
            if tokens is not None and tokens < 0:
                raise ValueError("token 수는 음수일 수 없습니다.")


@dataclass(frozen=True, slots=True)
class AssistantDebugTurn:
    """임시 Voice 디버그 화면에 공개할 성공 turn 요약이다."""

    completed_at: datetime
    user_text: str
    spoken_text: str
    request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    output_item_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AssistantDebugSnapshot:
    """provider payload를 제외한 현재 local session 관측값이다."""

    session_id: str
    completed_turns: int
    history_items: int
    history_item_types: tuple[str, ...]
    turns: tuple[AssistantDebugTurn, ...]
