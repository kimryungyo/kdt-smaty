"""프로세스에서 하나만 사용하는 AppContainer를 보관하고 조회한다."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from smart_desk.core.exceptions import (
    ContainerAlreadyInitializedError,
    ContainerNotInitializedError,
)
from smart_desk.core.runtime import RuntimeState
from smart_desk.core.task_manager import TaskManager

if TYPE_CHECKING:
    from smart_desk.config.settings import Settings
    from smart_desk.modules.dashboard.service import DashboardService
    from smart_desk.modules.desk.controller import DeskController
    from smart_desk.modules.desk.height_monitor import DeskHeightMonitor
    from smart_desk.modules.desk.relay import RelayClient
    from smart_desk.modules.mqtt.client import MqttClient
    from smart_desk.modules.profiles.repository import ProfileRepository
    from smart_desk.modules.assistant.service import AssistantService
    from smart_desk.modules.voice.service import VoiceService
    from smart_desk.storage import SQLiteDatabase


class LifecycleResource(Protocol):
    """컨테이너가 시작하고 종료할 수 있는 공유 자원 인터페이스다."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ResourceRegistration:
    """공유 자원의 시작·종료 순서를 정의한다."""

    name: str
    resource: LifecycleResource
    startup_order: int = 100
    shutdown_order: int = 100


@dataclass(slots=True)
class AppContainer:
    """프로세스 전체에서 공유하는 설정과 서비스 객체를 소유한다."""

    settings: Settings
    runtime: RuntimeState
    task_manager: TaskManager
    database: SQLiteDatabase
    profiles: ProfileRepository
    dashboard: DashboardService
    mqtt: MqttClient
    height_monitor: DeskHeightMonitor
    relay: RelayClient
    desk: DeskController
    assistant: AssistantService | None = None
    voice: VoiceService | None = None
    resources: list[ResourceRegistration] = field(default_factory=list)
    started_resources: list[ResourceRegistration] = field(default_factory=list)

    def register(self, registration: ResourceRegistration) -> None:
        """애플리케이션 수명주기에서 관리할 공유 자원을 등록한다."""

        if any(item.name == registration.name for item in self.resources):
            raise ValueError(f"공유 자원 '{registration.name}'이(가) 이미 등록되었습니다.")
        self.resources.append(registration)


_container: AppContainer | None = None


def install_container(container: AppContainer) -> None:
    """프로세스의 AppContainer를 한 번만 설치한다."""

    global _container
    if _container is not None:
        raise ContainerAlreadyInitializedError("AppContainer가 이미 초기화되었습니다.")
    _container = container


def get_container() -> AppContainer:
    """초기화된 프로세스 AppContainer를 반환한다."""

    if _container is None:
        raise ContainerNotInitializedError("AppContainer가 초기화되지 않았습니다.")
    return _container


def is_container_ready() -> bool:
    """컨테이너가 설치되고 애플리케이션이 READY인지 반환한다."""

    return _container is not None and _container.runtime.snapshot().ready


def reset_container() -> None:
    """테스트가 격리된 컨테이너를 설치할 수 있도록 전역 참조를 비운다."""

    global _container
    _container = None
