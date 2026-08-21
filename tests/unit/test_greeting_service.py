"""인사 대기 시간이 프로세스를 다시 띄워도 이어지는지 확인한다."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from smart_desk.modules.assistant.greeting import GreetingService


PROFILE = "profile-" + "a" * 32


class FakeProfile:
    name = "홍길동"


class FakeProfiles:
    async def get_profile(self, _profile_id: str) -> FakeProfile:
        return FakeProfile()


class FakeSynthesizer:
    def stream(self, text: str):  # type: ignore[no-untyped-def]
        async def chunks():
            yield text.encode()

        return chunks()


class FakeVoice:
    def __init__(self, *, speaks: bool = True) -> None:
        self.spoken = 0
        self._speaks = speaks

    async def announce(self, chunks) -> bool:  # type: ignore[no-untyped-def]
        async for _ in chunks:
            pass
        if not self._speaks:
            return False
        self.spoken += 1
        return True


def service_for(state_file: Path, voice: FakeVoice, now) -> GreetingService:
    # api_key를 비워 두면 날씨를 찾지 않고 이름만 부르는 인사로 떨어진다.
    return GreetingService(
        voice=voice, profiles=FakeProfiles(), synthesizer=FakeSynthesizer(),
        api_key="", model="test-model", cooldown_seconds=1800.0,
        state_file=state_file, now=now,
    )


async def _settle(service: GreetingService) -> None:
    task = service._task
    if task is not None:
        await task


async def test_cooldown_survives_a_restart(tmp_path: Path) -> None:
    state = tmp_path / "greeting_state.json"
    clock = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)

    first = FakeVoice()
    service = service_for(state, first, lambda: clock)
    service.greet(PROFILE)
    await _settle(service)
    assert first.spoken == 1

    # 프로세스가 다시 뜬 셈이다. 같은 파일을 읽는 새 인스턴스를 만든다.
    clock += timedelta(minutes=10)
    second = FakeVoice()
    restarted = service_for(state, second, lambda: clock)
    restarted.greet(PROFILE)
    await _settle(restarted)
    assert second.spoken == 0, "다시 떠도 30분 안에는 인사하지 않는다"

    # 30분이 지나면 새 방문으로 보고 다시 인사한다.
    clock += timedelta(minutes=21)
    third = FakeVoice()
    later = service_for(state, third, lambda: clock)
    later.greet(PROFILE)
    await _settle(later)
    assert third.spoken == 1


async def test_a_skipped_greeting_retries_later_not_immediately(tmp_path: Path) -> None:
    """말할 자리가 아니어서 넘어가도 곧바로 다시 말을 걸지는 않는다."""

    state = tmp_path / "greeting_state.json"
    clock = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)

    busy = FakeVoice(speaks=False)
    service = service_for(state, busy, lambda: clock)
    service.greet(PROFILE)
    await _settle(service)

    # 곧바로 다시 부르면 조용하다.
    service.greet(PROFILE)
    await _settle(service)
    assert busy.spoken == 0

    # 짧은 재시도 간격이 지나면 다시 시도한다.
    clock += timedelta(minutes=3)
    ready = FakeVoice()
    retry = service_for(state, ready, lambda: clock)
    retry.greet(PROFILE)
    await _settle(retry)
    assert ready.spoken == 1


async def test_stop_blocks_new_greeting_tasks(tmp_path: Path) -> None:
    service = service_for(
        tmp_path / "greeting_state.json",
        FakeVoice(),
        lambda: datetime(2026, 8, 19, 9, 0, tzinfo=UTC),
    )

    await service.stop()
    service.greet(PROFILE)

    assert service._task is None


def test_weather_uses_the_local_date_not_utc(tmp_path: Path) -> None:
    """컨테이너가 UTC로 돌아도 현지 날짜로 찾아야 한다.

    UTC 기준 어제 날짜로 검색하면 지난 날씨라 결과가 잡히지 않고, 모델이
    "확인할 수 없다"고 답해 인사말에서 날씨가 통째로 빠졌다.
    """

    # 한국은 이미 19일 오전, UTC로는 아직 18일 저녁이다.
    utc_moment = datetime(2026, 8, 18, 22, 30, tzinfo=UTC)
    service = GreetingService(
        voice=FakeVoice(), profiles=FakeProfiles(), synthesizer=FakeSynthesizer(),
        api_key="", model="test-model", state_file=tmp_path / "state.json",
        location="시흥", timezone="Asia/Seoul", now=lambda: utc_moment,
    )
    assert service._local_now().strftime("%m월 %d일") == "08월 19일"


async def test_weather_that_could_not_be_confirmed_is_left_out(tmp_path: Path) -> None:
    """모델이 확인 실패를 문장으로 답하면 그대로 읽지 않고 뺀다."""

    service = GreetingService(
        voice=FakeVoice(), profiles=FakeProfiles(), synthesizer=FakeSynthesizer(),
        api_key="key", model="test-model", state_file=tmp_path / "state.json",
    )

    async def refuses() -> str:
        return "죄송하지만 현재 기온과 하늘 상태는 확인되지 않습니다."

    service._search_weather = refuses  # type: ignore[method-assign]
    assert await service._weather_line() is None
