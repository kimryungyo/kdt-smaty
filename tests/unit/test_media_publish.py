"""카메라 publisher 전용 실행 진입점 테스트."""

import asyncio

import pytest

from smart_desk.config.settings import Settings
from smart_desk.media_publish import build_publishers, run_publishers


def test_build_publishers_uses_only_enabled_camera_configuration() -> None:
    settings = Settings(
        media={
            "user": {
                "publish_enabled": True,
                "publish_url": "rtsp://media.example/user-cam",
            },
            "posture": {"publish_enabled": False},
        },
        _env_file=None,
    )

    publishers = build_publishers(settings)

    assert list(publishers) == ["user"]
    assert publishers["user"]._rtsp_url == (  # noqa: SLF001
        "rtsp://media.example/user-cam"
    )


def test_build_publishers_rejects_explicitly_disabled_camera() -> None:
    settings = Settings(_env_file=None)

    with pytest.raises(RuntimeError, match="비활성화된 카메라"):
        build_publishers(settings, ("posture",))


async def test_run_publishers_stops_every_started_publisher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    stop_event = asyncio.Event()

    class FakePublisher:
        async def start(self) -> None:
            events.append("start")
            stop_event.set()

        async def stop(self) -> None:
            events.append("stop")

        def is_running(self) -> bool:
            return True

    monkeypatch.setattr(
        "smart_desk.media_publish.build_publishers",
        lambda *_args, **_kwargs: {"user": FakePublisher()},
    )

    await run_publishers(Settings(_env_file=None), stop_event=stop_event)

    assert events == ["start", "stop"]
