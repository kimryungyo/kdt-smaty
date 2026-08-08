"""Dashboard service와 container accessor를 노출한다."""

from smart_desk.core.container import get_container
from smart_desk.modules.dashboard.service import DashboardService


def get_dashboard() -> DashboardService:
    """AppContainer가 소유한 dashboard service를 반환한다."""

    return get_container().dashboard


__all__ = ["DashboardService", "get_dashboard"]
