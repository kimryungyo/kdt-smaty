"""얼굴 인식이 끊긴 뒤에도 직전 등록 사용자를 기억하는지 검증한다."""

from datetime import UTC, datetime, timedelta

from smart_desk.modules.identity.models import SessionKind
from smart_desk.modules.identity.recent_user import RecentUserMemory
from smart_desk.modules.identity.session import CurrentUserSessionService


async def test_remembers_profile_after_session_ends() -> None:
    users = CurrentUserSessionService()
    memory = RecentUserMemory(current_user=users)
    await memory.start()
    await users.select(SessionKind.REGISTERED, "profile-a", "face")
    assert await memory.profile_id() == "profile-a"
    # 얼굴이 안 잡혀 session이 끝나도 직전 사용자를 그대로 쓴다.
    await users.end("vacant")
    assert await memory.profile_id() == "profile-a"
    await memory.stop()


async def test_live_session_always_wins_over_remembered_profile() -> None:
    users = CurrentUserSessionService()
    memory = RecentUserMemory(current_user=users)
    await memory.start()
    await users.select(SessionKind.REGISTERED, "profile-a", "face")
    await users.select(SessionKind.REGISTERED, "profile-b", "switch")
    assert await memory.profile_id() == "profile-b"
    await memory.stop()


async def test_forgets_profile_after_retention_expires() -> None:
    now = datetime(2026, 8, 21, tzinfo=UTC)
    clock = {"now": now}
    users = CurrentUserSessionService()
    memory = RecentUserMemory(
        current_user=users, retention_seconds=60, utc_now=lambda: clock["now"]
    )
    await memory.start()
    await users.select(SessionKind.REGISTERED, "profile-a", "face")
    await users.end("vacant")
    clock["now"] = now + timedelta(seconds=61)
    # 오래 비운 자리는 남의 자리일 수 있으므로 기억을 버린다.
    assert await memory.profile_id() is None
    await memory.stop()


async def test_anonymous_session_is_not_remembered() -> None:
    users = CurrentUserSessionService()
    memory = RecentUserMemory(current_user=users)
    await memory.start()
    await users.select(SessionKind.ANONYMOUS, None, "unknown-face")
    assert await memory.profile_id() is None
    await memory.stop()
