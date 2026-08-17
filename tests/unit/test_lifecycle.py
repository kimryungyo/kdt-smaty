"""공유 자원의 시작·안전 종료 순서 테스트."""

from smart_desk.config.settings import Settings
from smart_desk.core.container import AppContainer, ResourceRegistration
from smart_desk.core.lifecycle import shutdown_application, start_application
from smart_desk.core.runtime import ApplicationStatus, RuntimeState
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.dashboard import DashboardService
from smart_desk.modules.profiles import ActivityModeRepository, ProfileRepository


class FakeResource:
    """시작과 종료 호출 순서를 기록하는 테스트 공유 자원."""

    def __init__(self, name: str, events: list[str]) -> None:
        self._name = name
        self._events = events

    async def start(self) -> None:
        self._events.append(f"start:{self._name}")

    async def stop(self) -> None:
        self._events.append(f"stop:{self._name}")


class FailingResource(FakeResource):
    """시작 도중 실패해 이미 시작한 resource의 rollback을 검증한다."""

    async def start(self) -> None:
        await super().start()
        raise RuntimeError(f"start failed: {self._name}")


async def test_resources_follow_explicit_startup_and_shutdown_order() -> None:
    events: list[str] = []
    database = FakeResource("sqlite", events)
    profiles = ProfileRepository(database)  # type: ignore[arg-type]
    activity_modes = ActivityModeRepository(database)  # type: ignore[arg-type]
    mqtt = FakeResource("mqtt", events)
    height_monitor = FakeResource("desk", events)
    desk_controller = FakeResource("controller", events)
    voice = FakeResource("voice", events)
    dashboard = DashboardService(desk_controller, profiles)  # type: ignore[arg-type]
    container = AppContainer(
        settings=Settings(environment="test", _env_file=None),
        runtime=RuntimeState(),
        task_manager=TaskManager(),
        database=database,  # type: ignore[arg-type]
        profiles=profiles,
        activity_modes=activity_modes,
        dashboard=dashboard,
        mqtt=mqtt,  # type: ignore[arg-type]
        height_monitor=height_monitor,  # type: ignore[arg-type]
        relay=object(),  # type: ignore[arg-type]
        desk=desk_controller,  # type: ignore[arg-type]
    )
    container.register(
        ResourceRegistration(
            name="sqlite",
            resource=database,
            startup_order=5,
            shutdown_order=5,
        )
    )
    container.register(
        ResourceRegistration(
            name="voice",
            resource=voice,
            startup_order=70,
            shutdown_order=70,
        )
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
        "start:sqlite",
        "start:mqtt",
        "start:desk",
        "start:controller",
        "start:voice",
        "stop:desk",
        "stop:voice",
        "stop:controller",
        "stop:mqtt",
        "stop:sqlite",
    ]
    assert container.runtime.snapshot().status is ApplicationStatus.STOPPED


async def test_partial_start_failure_stops_started_resources_once_in_reverse_order() -> None:
    events: list[str] = []
    first = FakeResource("first", events)
    second = FakeResource("second", events)
    failing = FailingResource("failing", events)
    profiles = ProfileRepository(first)  # type: ignore[arg-type]
    activity_modes = ActivityModeRepository(first)  # type: ignore[arg-type]
    container = AppContainer(
        settings=Settings(environment="test", _env_file=None),
        runtime=RuntimeState(),
        task_manager=TaskManager(),
        database=first,  # type: ignore[arg-type]
        profiles=profiles,
        activity_modes=activity_modes,
        dashboard=DashboardService(first, profiles),  # type: ignore[arg-type]
        mqtt=second,  # type: ignore[arg-type]
        height_monitor=second,  # type: ignore[arg-type]
        relay=object(),  # type: ignore[arg-type]
        desk=first,  # type: ignore[arg-type]
    )
    container.register(ResourceRegistration("first", first, startup_order=10, shutdown_order=10))
    container.register(ResourceRegistration("second", second, startup_order=20, shutdown_order=20))
    container.register(ResourceRegistration("failing", failing, startup_order=30, shutdown_order=30))

    try:
        await start_application(container)
    except RuntimeError as error:
        assert str(error) == "start failed: failing"
    else:
        raise AssertionError("start_application() must propagate resource startup failure")

    assert events == [
        "start:first",
        "start:second",
        "start:failing",
        "stop:second",
        "stop:first",
    ]
    assert container.started_resources == []
    assert container.runtime.snapshot().status is ApplicationStatus.FAILED
