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
    OpenAiResponseStep,
)
from smart_desk.modules.assistant.openai import OpenAiGatewayPort, OpenAiTurnError
from smart_desk.modules.assistant.tooling import AssistantToolRegistry


LOGGER = logging.getLogger(__name__)

DEVELOPER_INSTRUCTIONS = """당신은 로컬 스마트 데스크의 음성 어시스턴트다.
기본 언어는 한국어다.
spoken_text는 즉시 들을 수 있는 짧은 1~2문장으로 작성한다.
한 문단으로 답하고 마크다운, 목록, URL과 긴 풀이를 읽지 않는다.
아직 연결되지 않은 Dashboard나 camera 기능을 사용했거나 화면에 표시했다고 말하지 않는다.
사용자가 조명이나 다른 장치의 상태 조회·변경을 요청하면 제공된 tool 중 정확히 맞는 tool을 사용한다.
제공되지 않은 기능을 실행했다고 말하지 않는다.
장치 변경은 tool result의 ok가 true일 때만 완료됐다고 말하고, false이면 실패 이유를 짧게 설명한다.
tool을 나중에 실행하겠다고 약속하지 않는다.
밝기는 0~100퍼센트로 말하고, 밝기 0퍼센트와 전원 끄기를 혼동하지 않는다.
불을 끄라는 요청에는 turn_off_wled를 사용한다.
불을 켜라는 요청에는 turn_on_wled를 사용한다.
여러 변경 중 일부만 성공하면 성공한 항목과 실패한 항목을 짧게 구분한다.
Desk 물리 제어를 수행할 tool은 현재 제공되지 않는다.
최종 응답마다 next_action과 decision_reason을 반드시 선택한다.
사용자의 즉시 답변이 꼭 필요하거나 spoken_text에서 직접 질문한 경우에만 WAIT_FOR_FOLLOWUP을 선택한다.
사용자가 답변·청취·대화 중단을 요청하면 짧게 확인하고 RETURN_TO_WAKE_WORD를 선택한다.
요청과 답변이 완결됐고 즉시 입력이 필요하지 않으면 RETURN_TO_WAKE_WORD를 선택한다.
사용자가 추가 질문을 할 가능성만으로 WAIT_FOR_FOLLOWUP을 선택하지 않는다.
애매하면 RETURN_TO_WAKE_WORD를 선택한다.
next_action이나 decision_reason을 spoken_text로 읽지 않는다."""


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
        tool_registry: AssistantToolRegistry | None = None,
        *,
        session_max_turns: int,
        max_tool_calls_per_turn: int = 3,
    ) -> None:
        self._gateway = gateway
        self._tool_registry = tool_registry or AssistantToolRegistry(())
        self._session_max_turns = session_max_turns
        self._max_tool_calls_per_turn = max_tool_calls_per_turn
        self._session = _AssistantSession(history=[], completed_turns=0)
        self._debug_turns: list[AssistantDebugTurn] = []
        self._lock = asyncio.Lock()

    async def reply(self, user_text: str) -> AssistantReply:
        normalized = normalize_text(user_text)
        if not normalized:
            raise ValueError("빈 user text는 처리할 수 없습니다.")

        async with self._lock:
            reset_session = (
                self._session.completed_turns >= self._session_max_turns
            )
            old_history = () if reset_session else tuple(self._session.history)
            pending: list[HistoryItem] = [
                *old_history,
                {"role": "user", "content": normalized},
            ]
            turn_output_items: list[HistoryItem] = []
            tool_names: list[str] = []
            request_ids: list[str] = []
            input_usages: list[int] = []
            output_usages: list[int] = []

            while True:
                step = await self._create_step(
                    pending=pending,
                    old_history=old_history,
                    user_text=normalized,
                )
                pending.extend(step.output_items)
                turn_output_items.extend(step.output_items)
                if step.request_id is not None:
                    request_ids.append(step.request_id)
                if step.input_tokens is not None:
                    input_usages.append(step.input_tokens)
                if step.output_tokens is not None:
                    output_usages.append(step.output_tokens)

                if not step.tool_calls:
                    if step.reply is None:  # DTO도 검사하지만 port 구현을 방어한다.
                        raise OpenAiTurnError(
                            stage="responses", code="structured_reply_invalid"
                        )
                    reply = step.reply
                    break

                for call in step.tool_calls:
                    if len(tool_names) >= self._max_tool_calls_per_turn:
                        raise OpenAiTurnError(
                            stage="responses", code="tool_call_limit_exceeded"
                        )
                    output = await self._tool_registry.execute(call)
                    output_item: HistoryItem = {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": output.model_dump_json(exclude_none=True),
                    }
                    pending.append(output_item)
                    turn_output_items.append(output_item)
                    tool_names.append(call.name)

            self._session.history = pending
            if reset_session:
                self._session.completed_turns = 1
                self._debug_turns.clear()
            else:
                self._session.completed_turns += 1
            total_input_tokens = sum(input_usages) if input_usages else None
            total_output_tokens = sum(output_usages) if output_usages else None
            self._debug_turns.append(
                AssistantDebugTurn(
                    completed_at=datetime.now(timezone.utc),
                    user_text=normalized,
                    spoken_text=reply.spoken_text,
                    next_action=reply.next_action,
                    decision_reason=reply.decision_reason,
                    request_id=request_ids[-1] if request_ids else None,
                    request_ids=tuple(request_ids),
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    output_item_types=tuple(
                        str(item.get("type", "unknown"))
                        for item in turn_output_items
                    ),
                    tool_names=tuple(tool_names),
                    tool_call_count=len(tool_names),
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
                    "request_id": request_ids[-1] if request_ids else None,
                    "input_tokens": total_input_tokens,
                    "output_tokens": total_output_tokens,
                    "tool_call_count": len(tool_names),
                    "next_action": reply.next_action,
                    "decision_reason": reply.decision_reason,
                },
            )
            return reply

    async def _create_step(
        self,
        *,
        pending: list[HistoryItem],
        old_history: tuple[HistoryItem, ...],
        user_text: str,
    ) -> OpenAiResponseStep:
        create_step = getattr(self._gateway, "create_response_step", None)
        if create_step is not None:
            return await create_step(
                input_items=tuple(pending),
                instructions=DEVELOPER_INSTRUCTIONS,
                tools=self._tool_registry.specs(),
            )
        # 이전 fake/adapter의 one-shot 계약만 지원하는 migration 경로다.
        return await self._gateway.create_response(  # type: ignore[attr-defined]
            history=old_history,
            user_text=user_text,
            instructions=DEVELOPER_INSTRUCTIONS,
        )

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
