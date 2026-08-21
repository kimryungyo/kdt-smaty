"""사용자를 알아본 순간 먼저 건네는 인사.

책상이 얼굴을 알아보면 이름을 부르고, 오늘 날씨와 그 사람이 관심 있어 하던 것을
한두 문장으로 얹는다. 날씨는 web search로 그때그때 찾고, 관심사는 프로필 기억에
쌓인 내용에서 가져온다.

말할 자리인지는 `VoiceService.announce`가 판단한다. 여기서는 무슨 말을 할지만
정하고, 자리가 아니면 조용히 지나간다. 인사는 있으면 좋은 것이지 꼭 되어야 하는
일이 아니므로, 어느 단계가 실패해도 위쪽으로 오류를 올리지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any, Protocol

from smart_desk.modules.voice.speech import SpeechSynthesisError


LOGGER = logging.getLogger(__name__)

# 같은 사람에게 다시 인사하기까지 두는 시간. 얼굴 인식은 자리를 잠깐 비워도
# 다시 잡히므로, 이 간격이 없으면 앉았다 일어설 때마다 말을 건다.
DEFAULT_COOLDOWN_SECONDS = 1800.0
# 인사말에 얹을 관심사 개수. 많이 넣으면 말이 길어진다.
MAX_INTERESTS = 5
# 날씨 검색은 웹을 다녀오므로 넉넉히 준다. 12초로는 자주 못 끝내 날씨가 빠졌다.
WEATHER_TIMEOUT_SECONDS = 30.0
# 문장을 다듬는 일은 도구를 쓰지 않아 빠르다.
COMPOSE_TIMEOUT_SECONDS = 15.0
# 날씨는 분 단위로 바뀌지 않는다. 이 시간 동안은 찾아 둔 문장을 다시 쓴다.
WEATHER_CACHE_SECONDS = 1800.0
# 말할 자리가 아니어서 넘어간 경우 이만큼 뒤부터 다시 시도한다. 아예 지우면
# session이 바뀔 때마다 다시 말을 걸어 대기 시간이 없는 것처럼 보인다.
SKIPPED_RETRY_SECONDS = 120.0

# 날씨는 따로 찾는다. 인사말과 한 번에 시키면 모델이 검색을 건너뛰고 날씨 없이
# 말을 만들어 버리는 일이 잦았다.
# 지시문이 길고 조건이 많으면 모델이 검색 결과를 두고도 "확인하지 못했다"로
# 빠져나간다. 짧고 곧게 시킨다.
WEATHER_INSTRUCTIONS = """\
오늘 날씨를 한국어 존댓말 한 문장으로 알려줘. 기온과 하늘 상태를 함께 넣고,
소리 내어 읽을 글이므로 목록·이모지·기호는 쓰지 않는다.
"""

GREETING_INSTRUCTIONS = """\
너는 스마트 책상의 음성 비서다. 방금 얼굴로 알아본 사용자에게 건넬 인사말을 쓴다.

규칙:
- 한국어 존댓말, 2~3문장, 소리 내어 읽을 글이므로 목록·이모지·기호를 쓰지 않는다.
- 첫 문장은 반드시 "{name}님 반갑습니다"로 시작한다.
- 날씨가 주어졌으면 그 내용을 자연스럽게 한 문장으로 옮긴다. 숫자를 바꾸지 않는다.
- 날짜는 소리 내어 읽지 않는다. "2026년 8월 19일"이 아니라 "오늘"이라고 말한다.
- 날씨가 주어지지 않았으면 날씨를 아예 언급하지 않는다. 모른다고 말하지도 않는다.
- 관심사가 주어졌으면 그중 하나만 골라 가볍게 한마디 덧붙인다. 없으면 생략한다.
- 주어지지 않은 사실을 지어내지 않는다.
"""


class VoiceAnnouncePort(Protocol):
    async def announce(self, chunks: Any) -> bool: ...


class SpeechSynthesizerPort(Protocol):
    def stream(self, text: str) -> Any: ...


class GreetingService:
    """알아본 사용자에게 인사를 건넨다."""

    def __init__(
        self,
        *,
        voice: VoiceAnnouncePort,
        profiles: Any,
        synthesizer: SpeechSynthesizerPort,
        api_key: str,
        model: str,
        memory: Any | None = None,
        location: str = "시흥",
        timezone: str = "Asia/Seoul",
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        state_file: Path | None = None,
        now=lambda: datetime.now(UTC),
    ) -> None:
        self._voice = voice
        self._profiles = profiles
        self._synthesizer = synthesizer
        self._api_key = api_key
        self._model = model
        self._memory = memory
        self._location = location
        # 날짜는 현지 기준이어야 한다. 컨테이너가 UTC로 돌면 어제 날씨를 찾게 되고,
        # 지난 날짜는 검색에 잡히지 않아 "확인할 수 없다"는 답이 돌아온다.
        self._timezone = timezone
        self._cooldown = cooldown_seconds
        # 벽시계를 쓴다. monotonic은 프로세스가 다시 뜨면 0부터라 재시작 직후
        # 다시 인사하게 된다. 마지막 인사 시각은 파일로도 남겨 둔다.
        self._now = now
        self._state_file = state_file
        self._last_greeted: dict[str, datetime] = self._load_state()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        # 찾아 둔 날씨 문장과 찾은 시각. 잠시 재사용해 인사가 늦어지지 않게 한다.
        self._weather_cache: tuple[str, datetime] | None = None

    async def start(self) -> None:
        self._stopping = False

    def greet(self, profile_id: str | None) -> None:
        """인사를 예약한다. 부르는 쪽을 붙잡지 않는다."""

        if self._stopping or not profile_id or not self._should_greet(profile_id):
            return
        if self._task is not None and not self._task.done():
            # 앞선 인사가 아직 말하는 중이다. 겹쳐 말하지 않는다.
            return
        self._mark_greeted(profile_id, self._now())
        self._task = asyncio.create_task(self._speak(profile_id), name="voice-greeting")

    def reset(self, profile_id: str) -> None:
        """쉬는 시간을 지운다. 다음 greet()가 바로 인사한다."""

        self._last_greeted.pop(profile_id, None)
        self._save_state()

    def _mark_greeted(self, profile_id: str, when: datetime) -> None:
        self._last_greeted[profile_id] = when
        self._save_state()

    def _load_state(self) -> dict[str, datetime]:
        """지난 인사 시각을 읽는다. 없거나 깨져 있으면 빈 채로 시작한다."""

        if self._state_file is None or not self._state_file.exists():
            return {}
        try:
            raw = json.loads(self._state_file.read_text(encoding="utf-8"))
            return {
                str(key): datetime.fromisoformat(str(value))
                for key, value in dict(raw).items()
            }
        except Exception:
            LOGGER.info(
                "지난 인사 기록을 읽지 못해 비운 채로 시작합니다.",
                extra={"component": "assistant.greeting", "event": "greeting_state_unreadable"},
            )
            return {}

    def _save_state(self) -> None:
        """다음에 프로세스가 다시 떠도 대기 시간이 이어지게 남긴다."""

        if self._state_file is None:
            return
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {key: value.isoformat() for key, value in self._last_greeted.items()}
            self._state_file.write_text(json.dumps(payload), encoding="utf-8")
        except Exception:
            LOGGER.info(
                "인사 기록을 남기지 못했습니다.",
                extra={"component": "assistant.greeting", "event": "greeting_state_unwritable"},
            )

    async def stop(self) -> None:
        self._stopping = True
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    def _should_greet(self, profile_id: str) -> bool:
        last = self._last_greeted.get(profile_id)
        if last is None:
            return True
        return (self._now() - last).total_seconds() >= self._cooldown

    async def _speak(self, profile_id: str) -> None:
        try:
            name = await self._name_of(profile_id)
            if name is None:
                return
            interests = await self._interests_of(profile_id)
            text = await self._compose(name, interests)
            spoke = await self._voice.announce(self._synthesizer.stream(text))
            LOGGER.info(
                "인사말을 건넸습니다." if spoke else "지금은 인사할 자리가 아니었습니다.",
                extra={
                    "component": "assistant.greeting",
                    "event": "greeting_spoken" if spoke else "greeting_skipped",
                    "profile_id": profile_id,
                },
            )
            if not spoke:
                self._allow_retry_soon(profile_id)
        except asyncio.CancelledError:
            raise
        except SpeechSynthesisError as error:
            self._allow_retry_soon(profile_id)
            LOGGER.warning(
                "인사말을 소리로 바꾸지 못했습니다.",
                extra={"component": "assistant.greeting", "event": "greeting_tts_failed",
                       "error": error.code},
            )
        except Exception as error:
            self._allow_retry_soon(profile_id)
            LOGGER.warning(
                "인사말을 건네지 못했습니다.",
                extra={"component": "assistant.greeting", "event": "greeting_failed",
                       "error": str(error)},
            )

    def _allow_retry_soon(self, profile_id: str) -> None:
        """말하지 못했으면 잠시 뒤 다시 시도한다.

        기록을 아예 지우면 session이 바뀔 때마다 곧바로 다시 말을 걸어, 대기
        시간이 없는 것처럼 보인다. 짧은 간격만 두고 물러난다.
        """

        retreat = self._cooldown - SKIPPED_RETRY_SECONDS
        self._mark_greeted(profile_id, self._now() - timedelta(seconds=max(retreat, 0.0)))

    async def _name_of(self, profile_id: str) -> str | None:
        try:
            profile = await self._profiles.get_profile(profile_id)
        except Exception:
            return None
        name = (getattr(profile, "name", "") or "").strip()
        return name or None

    async def _interests_of(self, profile_id: str) -> list[str]:
        """프로필 기억에서 관심사로 쓸 문장을 모은다. 없으면 빈 목록이다."""

        if self._memory is None:
            return []
        try:
            entries = await self._memory.list_profile(profile_id)
        except Exception:
            return []
        interests: list[str] = []
        for entry in entries:
            text = str(entry.get("memory") or entry.get("text") or "").strip()
            if text:
                interests.append(text)
            if len(interests) >= MAX_INTERESTS:
                break
        return interests

    async def _compose(self, name: str, interests: list[str]) -> str:
        """날씨를 찾아 인사말을 쓴다. 실패하면 이름만 부르는 인사로 돌아간다."""

        fallback = f"{name}님 반갑습니다."
        if not self._api_key.strip():
            return fallback
        weather = await self._weather_line()
        if weather is None and not interests:
            # 얹을 내용이 없으면 굳이 한 번 더 부르지 않는다.
            return fallback
        try:
            written = await asyncio.wait_for(
                self._write_greeting(name, interests, weather), timeout=COMPOSE_TIMEOUT_SECONDS
            )
        except (asyncio.TimeoutError, Exception) as error:  # noqa: B014 - 어떤 실패든 인사는 이어간다
            LOGGER.info(
                "인사말을 다듬지 못해 짧게 인사합니다.",
                extra={"component": "assistant.greeting", "event": "greeting_compose_failed",
                       "error": str(error)},
            )
            return fallback
        written = (written or "").strip()
        return written or fallback

    async def _weather_line(self) -> str | None:
        """오늘 날씨 한 문장. 찾지 못하면 None.

        찾아 둔 문장은 잠시 재사용한다. 날씨는 분 단위로 바뀌지 않고, 매번
        웹을 다녀오면 인사가 늦어진다.
        """

        cached = self._weather_cache
        if cached is not None:
            line, stored_at = cached
            if (self._now() - stored_at).total_seconds() < WEATHER_CACHE_SECONDS:
                return line
        try:
            line = await asyncio.wait_for(
                self._search_weather(), timeout=WEATHER_TIMEOUT_SECONDS
            )
        except (asyncio.TimeoutError, Exception) as error:  # noqa: B014 - 날씨는 없어도 인사는 한다
            LOGGER.info(
                "날씨를 확인하지 못했습니다.",
                extra={"component": "assistant.greeting", "event": "greeting_weather_failed",
                       "error": str(error)},
            )
            return None
        line = (line or "").strip()
        if not line or any(mark in line for mark in ("확인되지", "확인할 수 없", "죄송")):
            LOGGER.info(
                "날씨를 확인하지 못해 인사말에서 뺍니다.",
                extra={"component": "assistant.greeting", "event": "greeting_weather_missing"},
            )
            return None
        self._weather_cache = (line, self._now())
        return line

    def _local_now(self) -> datetime:
        """날씨를 찾을 때 쓸 현지 시각."""

        try:
            return self._now().astimezone(ZoneInfo(self._timezone))
        except Exception:
            return self._now()

    async def _search_weather(self) -> str:
        """웹 검색을 강제해 오늘 날씨를 한 문장으로 받는다."""

        from agents import Agent, ModelSettings, Runner, WebSearchTool
        from agents.models.openai_responses import OpenAIResponsesModel
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._api_key)
        try:
            agent = Agent(
                name="Smart Desk Weather",
                model=OpenAIResponsesModel(self._model, client),
                instructions=WEATHER_INSTRUCTIONS,
                tools=[WebSearchTool()],
                # 검색을 강제한다. 그냥 두면 검색 없이 답을 지어내거나 날씨를 뺀다.
                model_settings=ModelSettings(tool_choice="required"),
            )
            today = self._local_now().strftime("%Y년 %m월 %d일")
            result = await Runner.run(agent, f"{today} {self._location} 날씨를 알려줘.")
            return str(getattr(result, "final_output", "") or "")
        finally:
            await client.close()

    async def _write_greeting(
        self, name: str, interests: list[str], weather: str | None
    ) -> str:
        """받은 재료로 인사말을 다듬는다. 도구를 쓰지 않아 빠르다."""

        from agents import Agent, Runner
        from agents.models.openai_responses import OpenAIResponsesModel
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._api_key)
        try:
            agent = Agent(
                name="Smart Desk Greeter",
                model=OpenAIResponsesModel(self._model, client),
                instructions=GREETING_INSTRUCTIONS.format(name=name),
            )
            lines = [f"사용자 이름: {name}"]
            # 모르면 아예 넣지 않는다. "확인되지 않았습니다"를 소리 내어 말하게
            # 두면 인사말이 어색해진다.
            if weather:
                lines.append(f"날씨: {weather}")
            lines.append(("관심사: " + " / ".join(interests)) if interests else "관심사: 알려진 것 없음")
            result = await Runner.run(agent, "\n".join(lines))
            return str(getattr(result, "final_output", "") or "")
        finally:
            await client.close()
