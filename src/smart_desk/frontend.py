"""React production build를 FastAPI의 정적 frontend로 연결한다."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI

from smart_desk.config.settings import Settings


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


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

    application.frontend("/", directory=directory, fallback="index.html")


def _resolve_frontend_directory(configured: Path) -> Path:
    """상대 frontend 경로를 프로젝트 루트 기준 절대 경로로 바꾼다."""

    if configured.is_absolute():
        return configured
    return PROJECT_ROOT / configured

