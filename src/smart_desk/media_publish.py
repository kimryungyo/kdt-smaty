"""Desk 서버 없이 설정된 로컬 카메라만 MediaMTX에 송출한다."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
import logging
import signal
from typing import Literal

from smart_desk.config.settings import CameraMediaSettings, Settings, get_settings
from smart_desk.core.logging import configure_logging
from smart_desk.modules.media import WebRtcCameraPublisher


LOGGER = logging.getLogger(__name__)
CameraName = Literal["user"]
CAMERA_NAMES: tuple[CameraName, ...] = ("user",)
PROCESS_CHECK_INTERVAL_SECONDS = 0.5


def build_publishers(
    settings: Settings,
    requested_names: Sequence[CameraName] = (),
) -> dict[CameraName, WebRtcCameraPublisher]:
    """요청 범위 안에서 publish가 활성화된 카메라를 생성한다."""

    configurations: dict[CameraName, CameraMediaSettings] = {
        "user": settings.media.user,
    }
    selected_names = tuple(dict.fromkeys(requested_names)) or CAMERA_NAMES
    explicitly_disabled = [
        name
        for name in selected_names
        if requested_names and not configurations[name].publish_enabled
    ]
    if explicitly_disabled:
        disabled = ", ".join(explicitly_disabled)
        raise RuntimeError(
            f"publish가 비활성화된 카메라를 요청했습니다: {disabled}"
        )

    publishers: dict[CameraName, WebRtcCameraPublisher] = {}
    for name in selected_names:
        camera = configurations[name]
        if not camera.publish_enabled:
            continue
        publishers[name] = WebRtcCameraPublisher(
            name=name,
            device=camera.device,
            whip_url=camera.publish_url,
            input_format=camera.input_format,
            width=camera.width,
            height=camera.height,
            fps=camera.fps,
            reconnect_interval_seconds=settings.media.reconnect_interval_seconds,
        )

    if not publishers:
        raise RuntimeError("publish가 활성화된 카메라가 없습니다.")
    return publishers


async def run_publishers(
    settings: Settings,
    requested_names: Sequence[CameraName] = (),
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    """선택한 publisher를 시작하고 종료 신호까지 감시한다."""

    configure_logging(settings.log_level)
    publishers = build_publishers(settings, requested_names)
    started: list[tuple[CameraName, WebRtcCameraPublisher]] = []
    resolved_stop_event = stop_event or asyncio.Event()
    if stop_event is None:
        _install_signal_handlers(resolved_stop_event)

    try:
        for name, publisher in publishers.items():
            await publisher.start()
            started.append((name, publisher))
        LOGGER.info(
            "카메라 publisher 전용 프로세스가 준비되었습니다.",
            extra={
                "component": "media",
                "event": "media_publish_ready",
            },
        )
        await _wait_until_stopped(resolved_stop_event, started)
    finally:
        for _name, publisher in reversed(started):
            await publisher.stop()


async def _wait_until_stopped(
    stop_event: asyncio.Event,
    publishers: Sequence[tuple[CameraName, WebRtcCameraPublisher]],
) -> None:
    while not stop_event.is_set():
        stopped_names = [name for name, publisher in publishers if not publisher.is_running()]
        if stopped_names:
            stopped = ", ".join(stopped_names)
            raise RuntimeError(
                f"카메라 publisher가 예기치 않게 종료되었습니다: {stopped}"
            )
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=PROCESS_CHECK_INTERVAL_SECONDS,
            )
        except TimeoutError:
            continue


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_number, stop_event.set)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SMART DESK FIN의 활성화된 카메라를 MediaMTX에 송출합니다."
    )
    parser.add_argument(
        "--camera",
        action="append",
        choices=CAMERA_NAMES,
        default=[],
        help=(
            "송출할 카메라를 제한합니다. 생략하면 publish가 활성화된 카메라 "
            "전부를 사용합니다."
        ),
    )
    return parser


def main() -> None:
    parser = _build_parser()
    arguments = parser.parse_args()
    try:
        asyncio.run(run_publishers(get_settings(), arguments.camera))
    except RuntimeError as error:
        parser.exit(1, f"카메라 publisher를 실행하지 못했습니다: {error}\n")


if __name__ == "__main__":
    main()
