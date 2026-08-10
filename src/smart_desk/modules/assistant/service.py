"""process-memory voice session과 Responses history transaction을 관리한다."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import unicodedata

from smart_desk.modules.assistant.models import AssistantReply, HistoryItem
from smart_desk.modules.assistant.openai import OpenAiGatewayPort


LOGGER = logging.getLogger(__name__)

DEVELOPER_INSTRUCTIONS = """당신은 로컬 스마트 데스크의 음성 어시스턴트다.
기본 언어는 한국어다.
spoken_text는 즉시 들을 수 있는 짧은 1~2문장으로 작성한다.
한 문단으로 답하고 마크다운, 목록, URL과 긴 풀이를 읽지 않는다.
아직 연결되지 않은 Dashboard나 camera 기능을 사용했거나 화면에 표시했다고 말하지 않는다.
현재 정보나 구현되지 않은 tool 결과를 추측하지 않는다.
Desk 물리 제어를 수행할 tool은 현재 제공되지 않는다."""


def normalize_text(value: str) -> str:
    """내용은 바꾸지 않고 Unicode와 공백만 안정적으로 정규화한다."""

    return " ".join(unicodedata.normalize("NFKC", value).split())


@dataclass(slots=True)
class _AssistantSession:
    history: list[HistoryItem]
    completed_turns: int


class AssistantService:
    """고정 voice:local session의 Responses history를 직렬화한다."""

    SESSION_ID = "voice:local"

    def __init__(
        self,
        gateway: OpenAiGatewayPort,
        *,
        session_max_turns: int,
    ) -> None:
        self._gateway = gateway
        self._session_max_turns = session_max_turns
        self._session = _AssistantSession(history=[], completed_turns=0)
        self._lock = asyncio.Lock()

    async def reply(self, user_text: str) -> AssistantReply:
        normalized = normalize_text(user_text)
        if not normalized:
            raise ValueError("빈 user text는 처리할 수 없습니다.")

        async with self._lock:
            if self._session.completed_turns >= self._session_max_turns:
                self._session.history.clear()
                self._session.completed_turns = 0

            old_history = tuple(self._session.history)
            turn = await self._gateway.create_response(
                history=old_history,
                user_text=normalized,
                instructions=DEVELOPER_INSTRUCTIONS,
            )
            self._session.history = [
                *old_history,
                {"role": "user", "content": normalized},
                *turn.output_items,
            ]
            self._session.completed_turns += 1
            LOGGER.info(
                "AI 음성 응답 history를 갱신했습니다.",
                extra={
                    "component": "assistant",
                    "event": "response_committed",
                    "session_id": self.SESSION_ID,
                    "completed_turns": self._session.completed_turns,
                    "history_items": len(self._session.history),
                    "request_id": turn.request_id,
                    "input_tokens": turn.input_tokens,
                    "output_tokens": turn.output_tokens,
                },
            )
            return turn.reply
