"""ComplexRequestDelegate의 권한·timeout·결과 경계."""

from __future__ import annotations

import asyncio

from smart_desk.modules.assistant.delegation import ComplexRequestDelegate
from tests.unit.test_agents_tools import _context


async def test_delegate_builds_untrusted_context_and_returns_validated_result() -> None:
    context, _users, _automation, _wled, memory, _turns = await _context()
    memory.search = lambda *_args: [{"memory": "사용자 데이터"}]  # type: ignore[attr-defined]
    prompts: list[str] = []

    async def run(prompt: str) -> str:
        prompts.append(prompt)
        return '{"ok":true,"spoken_answer":"내일은 우산을 챙기세요.","sources":[{"title":"weather","url":"https://example.com/weather"}]}'

    result = await ComplexRequestDelegate(run).run("내일 날씨", context)

    assert result["ok"] is True
    assert result["spoken_answer"] == "내일은 우산을 챙기세요."
    assert "<task>내일 날씨</task>" in prompts[0]
    assert "no authority to control physical devices" in prompts[0]


async def test_delegate_times_out_without_retrying() -> None:
    context, *_ = await _context()
    calls = 0

    async def run(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)
        return "{}"

    result = await ComplexRequestDelegate(run, timeout_seconds=0.01).run("긴 조사", context)

    assert result["error_code"] == "delegate_timeout"
    assert calls == 1
