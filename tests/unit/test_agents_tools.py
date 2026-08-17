"""SDK-level contracts for Smart Desk's context-bound function tools."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from agents.tool_context import ToolContext

from smart_desk.modules.assistant.agents_tools import SmartDeskAgentContext, build_smart_desk_tools
from smart_desk.modules.assistant.context import CurrentUserSessionManager
from smart_desk.modules.assistant.turns import AssistantTurnStore, TurnStatus
from smart_desk.modules.identity.models import SessionKind
from smart_desk.modules.identity.session import CurrentUserSessionService


class _Automation:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def stop_motion(self) -> None:
        self.calls.append(("stop_motion", (), {}))

    async def hold(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("hold", args, kwargs))

    async def set_target(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("set_target", args, kwargs))

    async def set_control_mode(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("set_control_mode", args, kwargs))

    async def set_activity_mode(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("set_activity_mode", args, kwargs))


class _Wled:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def _call(self, name: str, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append((name, args, kwargs))
        return SimpleNamespace(on=True, brightness=100, mode=None)

    async def turn_on(self, *args: Any, **kwargs: Any) -> SimpleNamespace: return await self._call("turn_on", *args, **kwargs)
    async def turn_off(self, *args: Any, **kwargs: Any) -> SimpleNamespace: return await self._call("turn_off", *args, **kwargs)
    async def set_brightness(self, *args: Any, **kwargs: Any) -> SimpleNamespace: return await self._call("set_brightness", *args, **kwargs)
    async def set_solid(self, *args: Any, **kwargs: Any) -> SimpleNamespace: return await self._call("set_solid", *args, **kwargs)
    async def set_effect(self, *args: Any, **kwargs: Any) -> SimpleNamespace: return await self._call("set_effect", *args, **kwargs)


class _Memory:
    def __init__(self) -> None: self.saved: list[tuple[str, str, bool]] = []
    async def remember(self, profile_id: str, fact: str, *, explicit: bool) -> None:
        self.saved.append((profile_id, fact, explicit))


async def _context(*, personalized: bool = True) -> tuple[SmartDeskAgentContext, CurrentUserSessionService, _Automation, _Wled, _Memory, AssistantTurnStore]:
    users = CurrentUserSessionService(session_id_factory=iter(["session-a", "session-b"]).__next__)
    sessions = CurrentUserSessionManager(users)
    turns = AssistantTurnStore(users)
    await sessions.start()
    await turns.start()
    selected = await users.select(SessionKind.REGISTERED, "profile-a", "test")
    captured = await sessions.capture(personalization_allowed=personalized)
    turn = await turns.create(selected.session_id, captured.profile_id)
    automation, wled, memory = _Automation(), _Wled(), _Memory()
    return (SmartDeskAgentContext(captured, sessions, memory, turns, turn.turn_id, turn.sequence, automation, wled), users, automation, wled, memory, turns)


async def _invoke(context: SmartDeskAgentContext, name: str, **arguments: Any) -> dict[str, object]:
    tool = next(tool for tool in build_smart_desk_tools() if tool.name == name)
    return await tool.on_invoke_tool(ToolContext(context, tool_name=name, tool_call_id="call", tool_arguments=json.dumps(arguments)), json.dumps(arguments))


async def test_mutation_tools_pass_expected_session_and_wled_effect() -> None:
    context, _users, automation, wled, _memory, _turns = await _context()
    assert (await _invoke(context, "hold_desk", direction="up"))["ok"] is True
    assert (await _invoke(context, "set_wled_effect", effect_id=2, palette_id=3))["ok"] is True
    assert automation.calls[0][2]["expected_session_id"] == "session-a"
    assert wled.calls[0] == ("set_effect", (2,), {"expected_session_id": "session-a", "palette_id": 3, "speed": 128, "intensity": 128, "color": None})


async def test_successful_final_turn_contains_only_compact_assistant_response() -> None:
    context, _users, _automation, _wled, _memory, turns = await _context()
    transcript = "사용자가 말한 민감한 원문"
    response = "  " + "답변 " * 80 + "  "

    context.append_assistant_response(response)
    await context.finish(TurnStatus.SUCCEEDED)
    latest = await turns.latest()

    assert latest is not None
    assert latest.summary == response.strip()[:200]
    assert latest.detail == response.strip()
    assert transcript not in (latest.summary or "")
    assert transcript not in (latest.detail or "")
    assert latest.phase.value == "FINAL" and latest.status is TurnStatus.SUCCEEDED


async def test_stop_survives_invalidation_but_every_other_mutation_is_blocked() -> None:
    context, users, automation, wled, _memory, _turns = await _context()
    await users.select(SessionKind.REGISTERED, "profile-b", "switch")
    assert (await _invoke(context, "stop_desk"))["ok"] is True
    assert (await _invoke(context, "set_desk_target", height_cm=70))["error"] == {"code": "session_mismatch"}
    assert (await _invoke(context, "turn_wled_on"))["error"] == {"code": "session_mismatch"}
    assert (await _invoke(context, "request_followup"))["error"] == {"code": "session_mismatch"}
    assert [call[0] for call in automation.calls] == ["stop_motion"]
    assert not wled.calls


async def test_null_context_blocks_every_non_stop_mutation() -> None:
    users = CurrentUserSessionService()
    sessions = CurrentUserSessionManager(users)
    turns = AssistantTurnStore(users)
    await sessions.start()
    await turns.start()
    captured = await sessions.capture()
    turn = await turns.create(None, None)
    automation, wled, memory = _Automation(), _Wled(), _Memory()
    context = SmartDeskAgentContext(captured, sessions, memory, turns, turn.turn_id, turn.sequence, automation, wled)
    for name, arguments in (
        ("hold_desk", {"direction": "up"}),
        ("set_desk_target", {"height_cm": 70}),
        ("set_control_mode", {"mode": "manual"}),
        ("set_activity_mode", {"key": "focus"}),
        ("turn_wled_on", {}),
        ("set_wled_effect", {"effect_id": 1}),
        ("remember_fact", {"fact": "likes tea"}),
        ("request_followup", {}),
    ):
        assert (await _invoke(context, name, **arguments))["ok"] is False
    assert (await _invoke(context, "stop_desk"))["ok"] is True
    assert [call[0] for call in automation.calls] == ["stop_motion"]
    assert not wled.calls and not memory.saved


async def test_current_personalization_block_allows_manual_mutation_but_not_memory() -> None:
    context, _users, automation, _wled, memory, _turns = await _context(personalized=False)
    assert (await _invoke(context, "set_desk_target", height_cm=70))["ok"] is True
    assert (await _invoke(context, "remember_fact", fact="likes tea"))["error"] == {"code": "memory_not_available"}
    assert automation.calls[0][2]["expected_session_id"] == "session-a"
    assert not memory.saved


async def test_remember_queues_only_for_current_personalized_context_and_followup_is_current_only() -> None:
    context, _users, _automation, _wled, memory, _turns = await _context()
    assert (await _invoke(context, "remember_fact", fact="likes tea"))["result"] == {"queued": True}
    assert context.explicit_memories == ["likes tea"]
    assert not memory.saved
    assert (await _invoke(context, "request_followup"))["result"] == {"followup_requested": True}
    assert context.followup_requested is True
