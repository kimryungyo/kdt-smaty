"""Profile-scoped Mem0 management routes keep PIN and namespace boundaries."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from smart_desk.api.routes import profiles as profiles_route


class _Profiles:
    async def get_pin_hash(self, _profile_id: str) -> None:
        return None


class _Dashboard:
    async def get_profile(self, _profile_id: str) -> object:
        return object()


class _Memory:
    def __init__(self) -> None:
        self.values = [{"id": "memory-1", "memory": "한국어 설명을 선호함", "user_id": "profile:a"}]
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def list_profile(self, profile_id: str) -> list[dict[str, object]]:
        self.calls.append(("list", (profile_id,)))
        return self.values

    async def remember(self, profile_id: str, content: str, **kwargs: object) -> bool:
        self.calls.append(("remember", (profile_id, content, kwargs)))
        return True

    async def update(self, profile_id: str, memory_id: str, content: str) -> dict[str, object]:
        self.calls.append(("update", (profile_id, memory_id, content)))
        return {"id": memory_id, "memory": content, "user_id": f"profile:{profile_id}"}

    async def delete(self, profile_id: str, memory_id: str) -> None:
        self.calls.append(("delete", (profile_id, memory_id)))


def test_profile_memory_routes_are_profile_scoped_and_content_bearing(monkeypatch) -> None:
    memory = _Memory()
    app = FastAPI()
    app.include_router(profiles_route.router)
    monkeypatch.setattr(profiles_route, "get_dashboard", lambda: _Dashboard())
    monkeypatch.setattr(
        profiles_route,
        "get_container",
        lambda: SimpleNamespace(profile_memory=memory, profiles=_Profiles(), current_user=None),
    )
    client = TestClient(app)

    assert client.post("/api/profiles/a/memories", json={"memory": "발표일은 8월 21일"}).status_code == 204
    listed = client.get("/api/profiles/a/memories")
    assert listed.status_code == 200
    assert listed.json() == [{"id": "memory-1", "memory": "한국어 설명을 선호함", "created_at": None, "updated_at": None, "metadata": None}]
    updated = client.patch("/api/profiles/a/memories/memory-1", json={"memory": "짧게 설명해 줌"})
    assert updated.status_code == 200 and updated.json()["memory"] == "짧게 설명해 줌"
    assert client.delete("/api/profiles/a/memories/memory-1").status_code == 204
    assert memory.calls == [
        ("remember", ("a", "발표일은 8월 21일", {"explicit": True, "source": "explicit_dashboard", "infer": False})),
        ("list", ("a",)),
        ("update", ("a", "memory-1", "짧게 설명해 줌")),
        ("delete", ("a", "memory-1")),
    ]
