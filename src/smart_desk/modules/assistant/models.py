"""AI 응답 schema와 provider history DTO를 정의한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from smart_desk.modules.assistant.tooling import AssistantToolCall


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
class OpenAiResponseStep:
    """Responses API 한 단계의 검증된 결과다."""

    reply: AssistantReply | None
    output_items: tuple[HistoryItem, ...]
    request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    tool_calls: tuple[AssistantToolCall, ...] = ()

    def __post_init__(self) -> None:
        if any(not isinstance(item, dict) for item in self.output_items):
            raise ValueError("OpenAI output item은 JSON object여야 합니다.")
        for tokens in (self.input_tokens, self.output_tokens):
            if tokens is not None and tokens < 0:
                raise ValueError("token 수는 음수일 수 없습니다.")
        if not self.tool_calls and self.reply is None:
            raise ValueError("tool call이 없는 response에는 최종 reply가 필요합니다.")


# 기존 import 사용자를 위한 이름이다. 새 코드는 step 계약을 사용한다.
OpenAiTurn = OpenAiResponseStep


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
    request_ids: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()
    tool_call_count: int = 0


@dataclass(frozen=True, slots=True)
class AssistantDebugSnapshot:
    """provider payload를 제외한 현재 local session 관측값이다."""

    session_id: str
    completed_turns: int
    history_items: int
    history_item_types: tuple[str, ...]
    turns: tuple[AssistantDebugTurn, ...]
