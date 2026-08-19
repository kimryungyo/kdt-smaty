"""React production build를 FastAPI의 정적 frontend로 연결한다."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse

from smart_desk.config.settings import Settings


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Vite가 내용 해시를 붙여 내보내는 build asset 경로다.
ASSET_PATH_PREFIX = "/assets"


def attach_frontend(application: FastAPI, settings: Settings) -> None:
    """설정에 따라 React 빌드 디렉터리를 SPA frontend로 등록한다."""

    dashboard = settings.dashboard
    if not dashboard.serve_frontend:
        return

    directory = _resolve_frontend_directory(dashboard.frontend_directory)
    index_file = directory / "index.html"
    if not index_file.is_file():
        detail = f"React 빌드 결과를 찾을 수 없습니다: {index_file}"
        if settings.environment == "production":
            raise RuntimeError(detail)
        LOGGER.warning(
            detail,
            extra={"component": "frontend", "event": "frontend_build_missing"},
        )
        return

    _attach_cache_headers(application)
    _attach_debug_routes(application, settings, index_file)
    application.frontend("/", directory=directory, fallback="index.html")


def _attach_debug_routes(
    application: FastAPI,
    settings: Settings,
    index_file: Path,
) -> None:
    """디버그 화면의 고정 진입점을 frontend와 별도 debug 서버에 연결한다.

    FastAPI frontend fallback은 브라우저의 ``Accept: text/html``에 따라 동작한다.
    운영자가 curl이나 단순 링크 검사로도 Vision 화면을 확인할 수 있도록 해당
    경로는 명시적으로 index를 반환한다. Voice debug는 같은 호스트의 전용 포트로
    이동시켜 기존 read-only 관측 페이지를 재사용한다.
    """

    async def vision_debug_page() -> FileResponse:
        return FileResponse(index_file, media_type="text/html")

    application.add_api_route(
        "/debug/vision",
        vision_debug_page,
        methods=["GET"],
        include_in_schema=False,
    )

    if not settings.voice_debug.enabled:
        return

    async def voice_debug_page(request: Request) -> RedirectResponse:
        target = request.url.replace(
            path="/",
            query="",
            fragment="",
            port=settings.voice_debug.port,
        )
        return RedirectResponse(str(target))

    application.add_api_route(
        "/debug/voice",
        voice_debug_page,
        methods=["GET"],
        include_in_schema=False,
    )


def _attach_cache_headers(application: FastAPI) -> None:
    """index.html은 매번 재검증하고, 해시가 붙은 asset은 오래 캐시하게 한다.

    Vite는 asset 파일 이름에 내용 해시를 넣으므로 오래 캐시해도 안전하다.
    반면 index.html이 캐시되면 배포 후에도 브라우저가 예전 번들을 계속
    불러와 새 화면이 반영되지 않는다.
    """

    @application.middleware("http")
    async def set_frontend_cache_control(request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        if "cache-control" in response.headers:
            return response
        if request.url.path.startswith(f"{ASSET_PATH_PREFIX}/"):
            response.headers["cache-control"] = "public, max-age=31536000, immutable"
        elif response.headers.get("content-type", "").startswith("text/html"):
            response.headers["cache-control"] = "no-cache, must-revalidate"
        return response


def _resolve_frontend_directory(configured: Path) -> Path:
    """상대 frontend 경로를 프로젝트 루트 기준 절대 경로로 바꾼다."""

    if configured.is_absolute():
        return configured
    return PROJECT_ROOT / configured
