from datetime import UTC, datetime
from smart_desk.modules.identity.models import SessionKind
from smart_desk.modules.identity.session import CurrentUserSessionService


async def test_transition_has_new_id_ordered_event_and_current_check() -> None:
    service = CurrentUserSessionService(utc_now=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    changes = []
    unsubscribe = await service.subscribe(changes.append)
    first = await service.select(SessionKind.ANONYMOUS, None, "TEST")
    second = await service.select(SessionKind.REGISTERED, "profile-a", "TEST")
    assert first.session_id != second.session_id
    assert await service.is_current(second.session_id)
    assert not await service.is_current(first.session_id)
    assert [event.sequence for event in changes] == [1, 2]
    await unsubscribe()
    await service.end("VACANT")
    assert len(changes) == 2


async def test_subscription_is_awaited_and_callback_failure_is_isolated() -> None:
    service = CurrentUserSessionService()
    changes = []

    def broken_callback(event) -> None:
        raise RuntimeError("observer failed")

    await service.subscribe(broken_callback)
    unsubscribe = await service.subscribe(changes.append)
    await service.select(SessionKind.ANONYMOUS, None, "TEST")
    assert [event.sequence for event in changes] == [1]
    await unsubscribe()
    await service.end("VACANT")
    assert len(changes) == 1
