"""FastAPI 애플리케이션을 생성하고 최상위 라우터를 연결한다."""

from fastapi import FastAPI

from smart_desk import __version__
from smart_desk.api.router import api_router
from smart_desk.bootstrap import build_container
from smart_desk.config.settings import Settings, get_settings
from smart_desk.core.container import AppContainer, install_container
from smart_desk.core.lifecycle import create_lifespan
from smart_desk.frontend import attach_frontend


def create_application(
    *,
    settings: Settings | None = None,
    container: AppContainer | None = None,
) -> FastAPI:
    """설정과 컨테이너로 FastAPI 애플리케이션을 만든다."""

    resolved_settings = settings or get_settings()
    resolved_container = container or build_container(resolved_settings)

    application = FastAPI(
        title=resolved_settings.app_name,
        version=__version__,
        lifespan=create_lifespan(resolved_container),
    )
    application.state.container = resolved_container
    application.include_router(api_router)
    attach_frontend(application, resolved_settings)
    install_container(resolved_container)
    return application
