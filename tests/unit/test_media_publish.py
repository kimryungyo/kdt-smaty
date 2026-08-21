"""WebRTC camera publisher 전용 실행 진입점 테스트."""

import asyncio

import pytest

from smart_desk.config.settings import Settings
from smart_desk.media_publish import build_publishers, run_publishers


def test_build_publishers_uses_only_enabled_camera_configuration() -> None:
    settings = Settings(media={
        "user": {"publish_enabled": True, "publish_url": "https://media.example/user-cam/whip"},
    }, _env_file=None)
    publishers = build_publishers(settings)
    assert list(publishers) == ["user"]
    assert publishers["user"]._endpoint.endswith("/user-cam/whip")  # noqa: SLF001


def test_build_publishers_rejects_explicitly_disabled_camera() -> None:
    with pytest.raises(RuntimeError, match="비활성화된 카메라"):
        build_publishers(Settings(_env_file=None), ("user",))


async def test_run_publishers_stops_every_started_publisher(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    stop_event = asyncio.Event()
    class FakePublisher:
        async def start(self): events.append("start"); stop_event.set()
        async def stop(self): events.append("stop")
        def is_running(self): return True
    monkeypatch.setattr("smart_desk.media_publish.build_publishers", lambda *_a, **_k: {"user": FakePublisher()})
    await run_publishers(Settings(_env_file=None), stop_event=stop_event)
    assert events == ["start", "stop"]
