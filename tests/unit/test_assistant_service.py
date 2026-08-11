"""voice:local Responses history commit과 rollback 테스트."""

import asyncio
import logging

import pytest

from smart_desk.modules.assistant.models import AssistantReply, OpenAiTurn
from smart_desk.modules.assistant.service import AssistantService


class FakeGateway:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.fail: BaseException | None = None
        self.wait: asyncio.Event | None = None

    async def create_response(self, **request: object) -> OpenAiTurn:
        self.requests.append(request)
        if self.wait is not None:
            await self.wait.wait()
        if self.fail is not None:
            raise self.fail
        turn_number = len(self.requests)
        return OpenAiTurn(
            reply=AssistantReply(spoken_text=f"응답 {turn_number}"),
            output_items=(
                {"type": "reasoning", "encrypted_content": f"reason-{turn_number}"},
                {"type": "message", "id": f"message-{turn_number}"},
            ),
            request_id=f"req-{turn_number}",
            input_tokens=turn_number,
            output_tokens=turn_number,
        )


async def test_success_commits_user_and_every_output_item() -> None:
    gateway = FakeGateway()
    assistant = AssistantService(gateway, session_max_turns=12)  # type: ignore[arg-type]

    await assistant.reply("  첫   질문  ")
    await assistant.reply("후속 질문")

    assert gateway.requests[0]["history"] == ()
    second_history = gateway.requests[1]["history"]
    assert second_history == (
        {"role": "user", "content": "첫 질문"},
        {"type": "reasoning", "encrypted_content": "reason-1"},
        {"type": "message", "id": "message-1"},
    )
    assert assistant._session.completed_turns == 2  # noqa: SLF001
    snapshot = assistant.get_debug_snapshot()
    assert snapshot.completed_turns == 2
    assert snapshot.history_items == 6
    assert snapshot.history_item_types == (
        "user",
        "reasoning",
        "message",
        "user",
        "reasoning",
        "message",
    )
    assert snapshot.turns[0].user_text == "첫 질문"
    assert snapshot.turns[0].spoken_text == "응답 1"
    assert snapshot.turns[0].output_item_types == ("reasoning", "message")


async def test_failure_and_cancellation_leave_existing_history_unchanged() -> None:
    gateway = FakeGateway()
    assistant = AssistantService(gateway, session_max_turns=12)  # type: ignore[arg-type]
    await assistant.reply("성공")
    original = list(assistant._session.history)  # noqa: SLF001

    gateway.fail = RuntimeError("provider failure")
    with pytest.raises(RuntimeError):
        await assistant.reply("실패")
    assert assistant._session.history == original  # noqa: SLF001

    gateway.fail = None
    gateway.wait = asyncio.Event()
    task = asyncio.create_task(assistant.reply("취소"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert assistant._session.history == original  # noqa: SLF001


async def test_session_resets_once_before_turn_after_maximum() -> None:
    gateway = FakeGateway()
    assistant = AssistantService(gateway, session_max_turns=1)  # type: ignore[arg-type]

    await assistant.reply("첫 질문")
    await assistant.reply("새 질문")

    assert gateway.requests[1]["history"] == ()
    assert assistant._session.completed_turns == 1  # noqa: SLF001
    assert assistant._session.history[0] == {  # noqa: SLF001
        "role": "user",
        "content": "새 질문",
    }
    assert [turn.user_text for turn in assistant.get_debug_snapshot().turns] == [
        "새 질문"
    ]


async def test_session_lock_serializes_concurrent_callers() -> None:
    gateway = FakeGateway()
    gateway.wait = asyncio.Event()
    assistant = AssistantService(gateway, session_max_turns=12)  # type: ignore[arg-type]

    first = asyncio.create_task(assistant.reply("첫 질문"))
    await asyncio.sleep(0)
    second = asyncio.create_task(assistant.reply("둘째 질문"))
    await asyncio.sleep(0)
    assert len(gateway.requests) == 1

    gateway.wait.set()
    await asyncio.gather(first, second)
    assert len(gateway.requests) == 2


async def test_logs_do_not_contain_user_or_reasoning_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gateway = FakeGateway()
    assistant = AssistantService(gateway, session_max_turns=12)  # type: ignore[arg-type]
    canary = "PRIVATE-TRANSCRIPT-CANARY"

    with caplog.at_level(logging.INFO):
        await assistant.reply(canary)

    rendered = " ".join(record.getMessage() for record in caplog.records)
    assert canary not in rendered
    assert "reason-1" not in rendered
