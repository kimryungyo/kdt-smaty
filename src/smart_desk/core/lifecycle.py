"""FastAPI lifespan과 공유 자원의 시작·종료 순서를 관리한다."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from smart_desk.core.container import AppContainer, ResourceRegistration
from smart_desk.core.logging import configure_logging
from smart_desk.core.runtime import ApplicationStatus


LOGGER = logging.getLogger(__name__)


async def start_application(container: AppContainer) -> None:
    """등록된 공유 자원을 순서대로 시작하고 준비 상태로 전환한다."""

    configure_logging(container.settings.log_level)
    container.runtime.mark(ApplicationStatus.STARTING, "애플리케이션을 시작하고 있습니다.")
    ordered = sorted(container.resources, key=lambda item: item.startup_order)
    try:
        for registration in ordered:
            await registration.resource.start()
            container.started_resources.append(registration)
    except Exception as error:
        container.runtime.mark_failed(
            f"공유 자원 '{registration.name}'을(를) 시작하지 못했습니다: {error}"
        )
        await _stop_started_resources(container)
        raise

    container.runtime.mark(ApplicationStatus.READY, "애플리케이션이 요청을 처리할 준비가 됐습니다.")
    LOGGER.info(
        "애플리케이션이 시작되었습니다.",
        extra={"component": "lifecycle", "event": "application_started"},
    )


async def shutdown_application(container: AppContainer) -> None:
    """공유 자원과 비동기 작업을 종료하고 정지 상태로 전환한다."""

    container.runtime.mark(ApplicationStatus.STOPPING, "애플리케이션을 종료하고 있습니다.")
    await _stop_started_resources(container)
    await container.task_manager.shutdown()
    container.runtime.mark(ApplicationStatus.STOPPED, "애플리케이션이 종료되었습니다.")
    LOGGER.info(
        "애플리케이션이 종료되었습니다.",
        extra={"component": "lifecycle", "event": "application_stopped"},
    )


async def _stop_started_resources(container: AppContainer) -> None:
    """시작된 공유 자원을 지정한 안전 종료 순서대로 중지한다."""

    ordered = sorted(
        container.started_resources,
        key=lambda item: item.shutdown_order,
        reverse=True,
    )
    for registration in ordered:
        try:
            await registration.resource.stop()
        except Exception:
            LOGGER.exception(
                "공유 자원을 종료하지 못했습니다.",
                extra={
                    "component": "lifecycle",
                    "event": "resource_stop_failed",
                    "resource": registration.name,
                },
            )
    container.started_resources.clear()


def create_lifespan(container: AppContainer):
    """지정한 컨테이너를 사용하는 FastAPI lifespan handler를 만든다."""

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        await start_application(container)
        try:
            yield
        finally:
            await shutdown_application(container)

    return lifespan

