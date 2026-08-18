"""Dashboard 유스케이스를 DeskController와 ProfileRepository에 위임한다."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from smart_desk.modules.dashboard.models import DashboardStatusResponse
from smart_desk.modules.desk.controller import DeskController
from smart_desk.modules.desk.models import Direction
from smart_desk.modules.profiles.activity_modes import (
    ActivityModeRepository,
    ActivityModeRepositoryError,
)
from smart_desk.modules.profiles.led_schedule import (
    DEFAULT_STUDY_SCHEDULE,
    DEFAULT_TIME_OF_DAY_SCHEDULE,
)
from smart_desk.modules.profiles.models import (
    ActivityModeCreate,
    Profile,
    ProfileCreate,
    ProfileUpdate,
)
from smart_desk.modules.profiles.repository import ProfileRepository

if TYPE_CHECKING:
    from smart_desk.modules.automation.service import AutomationService


LOGGER = logging.getLogger(__name__)

# 새 프로필에 기본으로 딸려 오는 작업 모드다. 이름 외의 나머지 값(LED·틸트)은
# 사용자가 설정 화면에서 직접 채운다. 실제 LED/틸트 하드웨어 연동은 별도
# 자동화 작업에서 이 필드를 읽어 연결한다.
# 새 프로필에 넣어 둘 작업 모드다. 조명 스케줄까지 함께 심어, 만들자마자
# 시간대에 맞는 빛으로 시작한다. 사용자가 색이나 밝기를 직접 고치면 그때
# 스케줄이 꺼지고 고른 값으로 고정된다.
DEFAULT_SEEDED_MODES: tuple[tuple[str, str, dict | None], ...] = (
    ("독서", "책을 읽을 때 사용하는 모드입니다.", DEFAULT_TIME_OF_DAY_SCHEDULE),
    ("공부", "공부에 집중할 때 사용하는 모드입니다.", DEFAULT_STUDY_SCHEDULE),
)


class DashboardService:
    """HTTP와 장치·저장 구현 사이의 작은 유스케이스 경계다."""

    def __init__(
        self,
        desk: DeskController,
        profiles: ProfileRepository,
        automation: AutomationService | None = None,
        *,
        activity_modes: ActivityModeRepository | None = None,
    ) -> None:
        self._desk = desk
        self._profiles = profiles
        self._automation = automation
        self._activity_modes = activity_modes

    def get_status(self) -> DashboardStatusResponse:
        return DashboardStatusResponse.from_snapshot(self._desk.get_snapshot())

    async def hold(self, direction: Direction) -> DashboardStatusResponse:
        if self._automation is not None:
            await self._automation.hold(direction)
            return self.get_status()
        if direction is Direction.UP:
            await self._desk.hold_up()
        else:
            await self._desk.hold_down()
        return self.get_status()

    async def stop_motion(self, reason: str) -> DashboardStatusResponse:
        if self._automation is not None:
            await self._automation.stop_motion(reason)
            return self.get_status()
        await self._desk.stop_motion(reason)
        return self.get_status()

    async def set_target(self, target_cm: float) -> DashboardStatusResponse:
        if self._automation is not None:
            await self._automation.set_target(target_cm)
            return self.get_status()
        await self._desk.set_target(target_cm)
        return self.get_status()

    async def cancel_target(self) -> DashboardStatusResponse:
        return await self.stop_motion("대시보드에서 목표 이동을 취소했습니다.")

    async def list_profiles(self) -> list[Profile]:
        return await self._profiles.list_profiles()

    async def get_profile(self, profile_id: str) -> Profile:
        return await self._profiles.get_profile(profile_id)

    async def create_profile(self, create: ProfileCreate) -> Profile:
        # 기본 작업 모드는 프로필 자체의 값에서 합성되므로, 조명 스케줄도
        # 프로필에 심어야 "기본"에서 시간대 조명이 돈다.
        if create.led_schedule is None and create.led_color is None:
            create = create.model_copy(update={"led_schedule": DEFAULT_TIME_OF_DAY_SCHEDULE})
        profile = await self._profiles.create_profile(create)
        if self._activity_modes is not None:
            await self._seed_default_modes(profile)
        return profile

    async def _seed_default_modes(self, profile: Profile) -> None:
        """독서·공부 기본 작업 모드를 만든다. 실패해도 프로필 생성은 유지한다."""

        assert self._activity_modes is not None
        for name, description, schedule in DEFAULT_SEEDED_MODES:
            try:
                await self._activity_modes.create_mode(
                    profile.id,
                    ActivityModeCreate(
                        name=name,
                        sitting_height_cm=profile.sitting_height_cm,
                        standing_height_cm=profile.standing_height_cm,
                        led_color=None,
                        led_schedule=schedule,
                        tilt_level=None,
                        description=description,
                    ),
                )
            except ActivityModeRepositoryError:
                LOGGER.warning(
                    "기본 작업 모드를 만들지 못했습니다.",
                    exc_info=True,
                    extra={
                        "component": "dashboard",
                        "event": "default_mode_seed_failed",
                        "profile_id": profile.id,
                        "mode_name": name,
                    },
                )

    async def update_profile(self, profile_id: str, update: ProfileUpdate) -> Profile:
        return await self._profiles.update_profile(profile_id, update)

    async def delete_profile(self, profile_id: str) -> None:
        await self._profiles.delete_profile(profile_id)
