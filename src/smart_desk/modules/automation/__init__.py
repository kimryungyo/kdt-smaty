"""서버 소유 책상 자동화 공개 경계."""

from smart_desk.core.container import get_container
from smart_desk.modules.automation.service import AutomationService


def get_automation() -> AutomationService:
    automation = get_container().automation
    if automation is None:
        raise RuntimeError("책상 자동화 서비스가 조립되지 않았습니다.")
    return automation


__all__ = ["AutomationService", "get_automation"]
