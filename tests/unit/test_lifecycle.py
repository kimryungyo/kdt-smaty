"""공유 자원의 시작·안전 종료 순서 테스트."""

from smart_desk.config.settings import Settings
from smart_desk.core.container import AppContainer, ResourceRegistration
from smart_desk.core.lifecycle import shutdown_application, start_application
from smart_desk.core.runtime import ApplicationStatus, RuntimeState
from smart_desk.core.task_manager import TaskManager


class FakeResource:
    """시작과 종료 호출 순서를 기록하는 테스트 공유 자원."""

    def __init__(self, name: str, events: list[str]) -> None:
        self._name = name
        self._events = events

    async def start(self) -> None:
        self._events.append(f"start:{self._name}")

    async def stop(self) -> None:
        self._events.append(f"stop:{self._name}")


async def test_resources_follow_explicit_startup_and_shutdown_order() -> None:
    events: list[str] = []
    mqtt = FakeResource("mqtt", events)
    height_monitor = FakeResource("desk", events)
    desk_controller = FakeResource("controller", events)
    container = AppContainer(
        settings=Settings(environment="test", _env_file=None),
        runtime=RuntimeState(),
        task_manager=TaskManager(),
        mqtt=mqtt,  # type: ignore[arg-type]
        height_monitor=height_monitor,  # type: ignore[arg-type]
        relay=object(),  # type: ignore[arg-type]
        desk=desk_controller,  # type: ignore[arg-type]
    )
    container.register(
        ResourceRegistration(
            name="mqtt",
            resource=mqtt,
            startup_order=10,
            shutdown_order=10,
        )
    )
    container.register(
        ResourceRegistration(
            name="controller",
            resource=desk_controller,
            startup_order=30,
            shutdown_order=30,
        )
    )
    container.register(
        ResourceRegistration(
            name="desk",
            resource=height_monitor,
            startup_order=20,
            shutdown_order=100,
        )
    )

    await start_application(container)
    await shutdown_application(container)

    assert events == [
        "start:mqtt",
        "start:desk",
        "start:controller",
        "stop:desk",
        "stop:controller",
        "stop:mqtt",
    ]
    assert container.runtime.snapshot().status is ApplicationStatus.STOPPED
