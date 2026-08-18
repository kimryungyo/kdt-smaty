from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from smart_desk.api.routes import assistant as assistant_route
from smart_desk.modules.assistant.context import BoundedSession, CurrentUserSessionManager
from smart_desk.modules.assistant.memory import ProfileMemoryError, ProfileMemoryService
from smart_desk.modules.assistant.turns import AssistantTurnStore, TurnPhase, TurnStatus
from smart_desk.modules.identity.models import SessionKind
from smart_desk.modules.identity.session import CurrentUserSessionService


async def test_bounded_session_implements_sdk_limit_and_cap_contract() -> None:
    session = BoundedSession("session-a", item_cap=3)
    await session.add_items([1, 2, 3, 4])

    assert await session.get_items() == [2, 3, 4]
    assert await session.get_items(None) == [2, 3, 4]
    assert await session.get_items(0) == []
    assert await session.get_items(-1) == [2, 3, 4]
    assert await session.get_items(2) == [3, 4]
    assert await session.pop_item() == 4
    await session.clear_session()
    assert await session.pop_item() is None

    with pytest.raises(ValueError):
        BoundedSession("bad", item_cap=0)


async def test_current_user_sessions_are_isolated_and_no_session_is_temporary() -> None:
    users = CurrentUserSessionService(session_id_factory=iter(["a", "b", "anon"]).__next__)
    manager = CurrentUserSessionManager(users, item_cap=2)
    await manager.start()

    a = await users.select(SessionKind.REGISTERED, "profile-a", "test")
    a_context = await manager.capture()
    await a_context.session.add_items(["a"])
    anonymous = await users.select(SessionKind.ANONYMOUS, None, "test")
    anonymous_context = await manager.capture()
    await anonymous_context.session.add_items(["anonymous"])
    b = await users.select(SessionKind.REGISTERED, "profile-b", "test")
    b_context = await manager.capture()

    assert a.session_id != b.session_id
    assert anonymous_context.session.session_id == anonymous.session_id
    assert await a_context.session.get_items() == []
    assert await anonymous_context.session.get_items() == []
    assert await b_context.session.get_items() == []

    await users.end("vacant")
    no_session_one = await manager.capture()
    no_session_two = await manager.capture()
    blocked = await manager.capture(personalization_allowed=False)
    assert no_session_one.session is not no_session_two.session
    assert blocked.session is not no_session_one.session
    assert blocked.session_id is None
    assert blocked.profile_id is None
    await manager.stop()


async def test_personalization_block_uses_temporary_history_but_keeps_current_session_id() -> None:
    users = CurrentUserSessionService(session_id_factory=iter(["a"]).__next__)
    manager = CurrentUserSessionManager(users)
    await manager.start()
    current = await users.select(SessionKind.REGISTERED, "profile-a", "test")

    context = await manager.capture(personalization_allowed=False)

    assert context.session_id == current.session_id
    assert context.profile_id is None
    assert context.personalized is False
    assert context.session.session_id == "temporary"
    await manager.stop()


async def test_user_change_cancels_runs_and_invalidates_generation() -> None:
    users = CurrentUserSessionService(session_id_factory=iter(["a", "b"]).__next__)
    manager = CurrentUserSessionManager(users)
    await manager.start()
    await users.select(SessionKind.REGISTERED, "profile-a", "test")
    context = await manager.capture()
    waiting = asyncio.create_task(asyncio.sleep(60))
    manager.register_run(waiting)

    await users.select(SessionKind.REGISTERED, "profile-b", "change")
    await asyncio.sleep(0)

    assert waiting.cancelled()
    await asyncio.gather(waiting, return_exceptions=True)
    assert not await manager.is_valid(context)
    await manager.stop()


async def test_null_session_context_requires_current_snapshot_to_remain_null() -> None:
    users = CurrentUserSessionService(session_id_factory=iter(["a"]).__next__)
    manager = CurrentUserSessionManager(users)
    await manager.start()
    context = await manager.capture()

    assert await manager.is_valid(context)
    await users.select(SessionKind.REGISTERED, "profile-a", "arrive")
    assert not await manager.is_valid(context)
    await manager.stop()


class _Backend:
    def __init__(self, *, search_result: object | None = None) -> None:
        self.added: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self.search_result = search_result if search_result is not None else {"results": []}
        self.search_calls: list[tuple[str, int, dict[str, str]]] = []
        self.updated: list[tuple[str, str]] = []
        self.deleted_ids: list[str] = []

    async def add(self, content: str, *, user_id: str, **_kwargs: object) -> None:
        self.added.append((content, user_id))

    async def search(
        self, query: str, *, top_k: int, filters: dict[str, str]
    ) -> object:
        self.search_calls.append((query, top_k, filters))
        return self.search_result

    async def delete_all(self, *, user_id: str) -> None:
        self.deleted.append(user_id)

    async def get_all(self, *, filters: dict[str, str], top_k: int = 20) -> object:
        assert top_k > 0
        assert "user_id" in filters
        if not isinstance(self.search_result, dict) or not isinstance(self.search_result.get("results"), list):
            return self.search_result
        return {
            "results": [
                value
                for value in self.search_result["results"]
                if not isinstance(value, dict) or value.get("user_id") in (None, filters["user_id"])
            ]
        }

    async def update(self, memory_id: str, *, text: str) -> object:
        self.updated.append((memory_id, text))
        return {"id": memory_id, "memory": text}

    async def delete(self, memory_id: str) -> None:
        self.deleted_ids.append(memory_id)


async def test_memory_is_explicit_scoped_and_normalizes_search_shapes() -> None:
    backend = _Backend(search_result={"results": [
        {"id": "memory-a", "memory": "tea", "user_id": "profile:a"},
        {"id": "memory-b", "memory": "desk", "user_id": "profile:a"},
    ]})
    memory = ProfileMemoryService(enabled=False, search_limit=1)
    memory._enabled = True  # noqa: SLF001 - fake backend injection boundary
    memory._memory = backend  # noqa: SLF001 - fake backend injection boundary

    await memory.remember("a", "raw transcript", explicit=False)
    await memory.remember("a", "likes tea", explicit=True)
    assert backend.added == [("likes tea", "profile:a")]
    assert (await memory.search("a", "query"))[0]["memory"] == "tea"
    assert backend.search_calls == [("query", 1, {"user_id": "profile:a"})]

    backend.search_result = {"results": [{"id": "memory-c", "memory": "other", "user_id": "profile:b"}]}
    assert (await memory.search("b", "query"))[0]["memory"] == "other"
    await memory.delete_profile("a")
    assert backend.deleted == ["profile:a"]


async def test_memory_from_config_accepts_sync_or_async_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    sync_backend = _Backend()

    class SyncMemory:
        @staticmethod
        def from_config(_config: object) -> _Backend:
            return sync_backend

    monkeypatch.setitem(sys.modules, "mem0", SimpleNamespace(AsyncMemory=SyncMemory))
    memory = ProfileMemoryService(enabled=True)
    await memory.remember("a", "remember", explicit=True)
    assert sync_backend.added == [("remember", "profile:a")]

    async_backend = _Backend()

    class AsyncMemory:
        @staticmethod
        async def from_config(_config: object) -> _Backend:
            return async_backend

    monkeypatch.setitem(sys.modules, "mem0", SimpleNamespace(AsyncMemory=AsyncMemory))
    memory = ProfileMemoryService(enabled=True)
    await memory.remember("b", "remember", explicit=True)
    assert async_backend.added == [("remember", "profile:b")]


async def test_memory_timeout_and_enabled_delete_failure_are_degraded_errors() -> None:
    class SlowBackend(_Backend):
        async def search(
            self, _query: str, *, top_k: int, filters: dict[str, str]
        ) -> object:
            await asyncio.sleep(1)
            return {"results": []}

        async def delete_all(self, *, user_id: str) -> None:
            raise RuntimeError("backend down")

    memory = ProfileMemoryService(enabled=True, timeout_seconds=0.01)
    memory._memory = SlowBackend()  # noqa: SLF001 - fake backend injection boundary
    with pytest.raises(ProfileMemoryError, match="profile_memory_search_failed"):
        await memory.search("a", "query")
    with pytest.raises(ProfileMemoryError, match="profile_memory_delete_failed"):
        await memory.delete_profile("a")

    disabled = ProfileMemoryService(enabled=False)
    assert await disabled.search("a", "query") == []
    with pytest.raises(ProfileMemoryError, match="profile_memory_unavailable"):
        await disabled.remember("a", "ignored", explicit=True)
    await disabled.delete_profile("a")


@pytest.mark.parametrize(
    "result",
    [None, [], {}, {"results": None}, {"results": ["not-a-memory"]}],
)
async def test_memory_rejects_every_malformed_v2_search_result(result: object) -> None:
    memory = ProfileMemoryService(enabled=True)
    backend = _Backend()
    backend.search_result = result
    memory._memory = backend  # noqa: SLF001

    with pytest.raises(ProfileMemoryError, match="profile_memory_search_failed"):
        await memory.search("a", "query")


async def test_memory_wraps_sync_method_errors_and_initialization_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SyncFailureBackend(_Backend):
        def add(self, _content: str, *, user_id: str) -> None:
            raise RuntimeError(user_id)

    memory = ProfileMemoryService(enabled=True)
    memory._memory = SyncFailureBackend()  # noqa: SLF001
    with pytest.raises(ProfileMemoryError, match="profile_memory_add_failed"):
        await memory.remember("a", "remember", explicit=True)

    class SlowMemory:
        @staticmethod
        async def from_config(_config: object) -> object:
            await asyncio.sleep(1)
            return _Backend()

    monkeypatch.setitem(sys.modules, "mem0", SimpleNamespace(AsyncMemory=SlowMemory))
    memory = ProfileMemoryService(enabled=True, timeout_seconds=0.01)
    with pytest.raises(ProfileMemoryError, match="profile_memory_unavailable"):
        await memory.search("a", "query")


async def test_memory_rejects_stale_or_oversized_writes_and_scopes_management() -> None:
    backend = _Backend(
        search_result={"results": [{"id": "memory-a", "memory": "likes tea", "user_id": "profile:a"}]}
    )
    memory = ProfileMemoryService(enabled=True, fact_limit=10)
    memory._memory = backend  # noqa: SLF001 - fake backend injection boundary

    with pytest.raises(ProfileMemoryError, match="profile_memory_policy_rejected"):
        await memory.remember("a", "this is too long", explicit=True)
    with pytest.raises(ProfileMemoryError, match="profile_memory_session_mismatch"):
        await memory.remember("a", "tea", explicit=True, is_valid=lambda: _false())

    assert (await memory.list_profile("a"))[0]["id"] == "memory-a"
    updated = await memory.update("a", "memory-a", "coffee")
    assert updated["memory"] == "coffee"
    await memory.delete("a", "memory-a")
    assert backend.updated == [("memory-a", "coffee")]
    assert backend.deleted_ids == ["memory-a"]


async def _false() -> bool:
    return False


async def test_memory_opens_circuit_after_repeated_backend_failure() -> None:
    class BrokenBackend(_Backend):
        async def search(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("unavailable")

    memory = ProfileMemoryService(
        enabled=True, circuit_failure_threshold=1, circuit_open_seconds=30
    )
    memory._memory = BrokenBackend()  # noqa: SLF001 - fake backend injection boundary
    with pytest.raises(ProfileMemoryError, match="profile_memory_search_failed"):
        await memory.search("a", "query")
    with pytest.raises(ProfileMemoryError, match="profile_memory_unavailable"):
        await memory.search("a", "query")
    assert memory.snapshot().status.value == "DEGRADED"


async def test_latest_turn_obeys_session_changes_and_terminal_ordering() -> None:
    users = CurrentUserSessionService(session_id_factory=iter(["a", "b"]).__next__)
    store = AssistantTurnStore(users)
    await store.start()
    a = await users.select(SessionKind.REGISTERED, "profile-a", "test")
    first = await store.create(a.session_id, "profile-a")
    second = await store.create(a.session_id, "profile-a")

    assert await store.update(first.turn_id, sequence=9, phase=TurnPhase.FINAL) is None
    assert await store.update(second.turn_id, sequence=3, phase=TurnPhase.FINAL, status=TurnStatus.SUCCEEDED)
    assert await store.update(second.turn_id, sequence=4, phase=TurnPhase.TOOL) is None
    assert (await store.latest()).turn_id == second.turn_id  # type: ignore[union-attr]

    await users.select(SessionKind.REGISTERED, "profile-b", "change")
    assert await store.latest() is None
    assert await store.update(second.turn_id, sequence=5, phase=TurnPhase.FINAL) is None
    await users.end("vacant")
    assert await store.latest() is None
    await store.stop()


async def test_null_session_turn_only_displays_before_any_user_change() -> None:
    users = CurrentUserSessionService(session_id_factory=iter(["a"]).__next__)
    store = AssistantTurnStore(users)
    await store.start()
    turn = await store.create(None, None)
    assert (await store.latest()).turn_id == turn.turn_id  # type: ignore[union-attr]

    await users.select(SessionKind.REGISTERED, "profile-a", "arrive")
    await users.end("leave")
    assert await store.latest() is None
    await store.stop()


async def test_latest_response_is_frozen_camel_case_and_preserves_null_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turn = SimpleNamespace(
        turn_id="turn-1",
        session_id=None,
        profile_id=None,
        phase=TurnPhase.FINAL,
        sequence=3,
        status=TurnStatus.SUCCEEDED,
        title="title",
        summary="summary",
        detail="detail",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        completed_at=datetime(2026, 1, 1, tzinfo=UTC),
        error_code=None,
    )

    async def latest() -> object:
        return turn

    app = FastAPI()
    app.include_router(assistant_route.router)
    monkeypatch.setattr(assistant_route, "get_container", lambda: SimpleNamespace(assistant_turns=SimpleNamespace(latest=latest)))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/assistant/latest")
    assert response.json()["turn"]["sessionId"] is None
    with pytest.raises(ValidationError):
        assistant_route.TurnResponse.model_validate({"turnId": "x", "unknown": True})
