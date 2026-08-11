"""process-memory voice session과 Responses history transaction을 관리한다."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import unicodedata

from smart_desk.modules.assistant.models import (
    AssistantDebugSnapshot,
    AssistantDebugTurn,
    AssistantReply,
    HistoryItem,
)
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
        self._debug_turns: list[AssistantDebugTurn] = []
        self._lock = asyncio.Lock()

    async def reply(self, user_text: str) -> AssistantReply:
        normalized = normalize_text(user_text)
        if not normalized:
            raise ValueError("빈 user text는 처리할 수 없습니다.")

        async with self._lock:
            if self._session.completed_turns >= self._session_max_turns:
                self._session.history.clear()
                self._session.completed_turns = 0
                self._debug_turns.clear()

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
            self._debug_turns.append(
                AssistantDebugTurn(
                    completed_at=datetime.now(timezone.utc),
                    user_text=normalized,
                    spoken_text=turn.reply.spoken_text,
                    request_id=turn.request_id,
                    input_tokens=turn.input_tokens,
                    output_tokens=turn.output_tokens,
                    output_item_types=tuple(
                        str(item.get("type", "unknown")) for item in turn.output_items
                    ),
                )
            )
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

    def get_debug_snapshot(self) -> AssistantDebugSnapshot:
        """현재 session을 provider 비밀값 없이 디버그용으로 복사한다."""

        return AssistantDebugSnapshot(
            session_id=self.SESSION_ID,
            completed_turns=self._session.completed_turns,
            history_items=len(self._session.history),
            history_item_types=tuple(
                str(item.get("type", item.get("role", "unknown")))
                for item in self._session.history
            ),
            turns=tuple(self._debug_turns),
        )
