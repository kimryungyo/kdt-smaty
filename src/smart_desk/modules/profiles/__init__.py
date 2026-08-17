"""프로필 모델, repository와 container accessor를 노출한다."""

from smart_desk.core.container import get_container
from smart_desk.modules.profiles.activity_modes import (
    ActivityModeConflictError,
    ActivityModeNotFoundError,
    ActivityModeOwnershipError,
    ActivityModeRepository,
    ActivityModeRepositoryError,
    generate_activity_mode_id,
)
from smart_desk.modules.profiles.models import (
    ActivityMode,
    ActivityModeCreate,
    ActivityModeUpdate,
    EffectiveActivityMode,
    Profile,
    ProfileCreate,
    ProfileUpdate,
)
from smart_desk.modules.profiles.repository import (
    ProfileConflictError,
    ProfileNotFoundError,
    ProfileRepository,
    ProfileRepositoryError,
    generate_profile_id,
)


def get_profiles() -> ProfileRepository:
    """AppContainer가 소유한 프로필 repository를 반환한다."""

    return get_container().profiles


def get_activity_modes() -> ActivityModeRepository:
    """AppContainer가 소유한 작업 모드 repository를 반환한다."""

    return get_container().activity_modes


__all__ = [
    "Profile",
    "ActivityMode",
    "ActivityModeConflictError",
    "ActivityModeCreate",
    "ActivityModeNotFoundError",
    "ActivityModeOwnershipError",
    "ActivityModeRepository",
    "ActivityModeRepositoryError",
    "ActivityModeUpdate",
    "EffectiveActivityMode",
    "ProfileConflictError",
    "ProfileCreate",
    "ProfileNotFoundError",
    "ProfileRepository",
    "ProfileRepositoryError",
    "ProfileUpdate",
    "generate_profile_id",
    "generate_activity_mode_id",
    "get_activity_modes",
    "get_profiles",
]
