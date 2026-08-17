"""Dashboard 유스케이스를 DeskController와 ProfileRepository에 위임한다."""

from __future__ import annotations

from typing import TYPE_CHECKING

from smart_desk.modules.dashboard.models import DashboardStatusResponse
from smart_desk.modules.desk.controller import DeskController
from smart_desk.modules.desk.models import Direction
from smart_desk.modules.profiles.models import Profile, ProfileCreate, ProfileUpdate
from smart_desk.modules.profiles.repository import ProfileRepository

if TYPE_CHECKING:
    from smart_desk.modules.automation.service import AutomationService


class DashboardService:
    """HTTP와 장치·저장 구현 사이의 작은 유스케이스 경계다."""

    def __init__(
        self,
        desk: DeskController,
        profiles: ProfileRepository,
        automation: AutomationService | None = None,
    ) -> None:
        self._desk = desk
        self._profiles = profiles
        self._automation = automation

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
        return await self._profiles.create_profile(create)

    async def update_profile(self, profile_id: str, update: ProfileUpdate) -> Profile:
        return await self._profiles.update_profile(profile_id, update)

    async def delete_profile(self, profile_id: str) -> None:
        await self._profiles.delete_profile(profile_id)
