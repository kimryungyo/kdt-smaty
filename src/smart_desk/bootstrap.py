"""애플리케이션이 사용할 singleton 객체를 한곳에서 조립한다."""

from smart_desk.config.settings import Settings
from smart_desk.core.container import AppContainer
from smart_desk.core.runtime import RuntimeState
from smart_desk.core.task_manager import TaskManager


def build_container(settings: Settings) -> AppContainer:
    """검증된 설정으로 프로세스의 공유 객체 컨테이너를 만든다."""

    runtime = RuntimeState()
    task_manager = TaskManager(
        on_critical_failure=lambda name, error: runtime.mark_failed(
            f"필수 작업 '{name}'이(가) 종료되었습니다: {error}"
        )
    )
    return AppContainer(
        settings=settings,
        runtime=runtime,
        task_manager=task_manager,
    )

