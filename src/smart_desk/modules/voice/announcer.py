"""글을 받아 스피커로 말해 주는 자리.

인사말이든 책상이 움직인다는 알림이든, 기기가 먼저 말을 거는 일은 모두 여기를
지난다. 실제로 말할 자리인지(대화 중이 아닌지)는 `VoiceService.announce`가
판단하고, 이 클래스는 글을 소리로 바꿔 그쪽에 넘기는 일만 한다.

알림은 있으면 좋은 것이지 꼭 되어야 하는 일이 아니다. 어느 단계가 실패해도
오류를 위로 올리지 않고 조용히 지나간다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from smart_desk.modules.voice.speech import SpeechSynthesisError


LOGGER = logging.getLogger(__name__)


class VoiceAnnouncePort(Protocol):
    async def announce(self, chunks: Any) -> bool: ...


class SpeechSynthesizerPort(Protocol):
    def stream(self, text: str) -> Any: ...


class SpeechAnnouncer:
    """한 번에 하나씩, 겹치지 않게 말한다."""

    def __init__(
        self,
        *,
        voice: VoiceAnnouncePort,
        synthesizer: SpeechSynthesizerPort,
    ) -> None:
        self._voice = voice
        self._synthesizer = synthesizer
        self._task: asyncio.Task[bool] | None = None
        self._stopping = False

    async def start(self) -> None:
        self._stopping = False

    async def say(self, text: str) -> bool:
        """말하고, 말했는지를 돌려준다."""

        spoken = text.strip()
        if self._stopping or not spoken:
            return False
        try:
            return await self._voice.announce(self._synthesizer.stream(spoken))
        except asyncio.CancelledError:
            raise
        except SpeechSynthesisError as error:
            LOGGER.warning(
                "알림을 소리로 바꾸지 못했습니다.",
                extra={"component": "voice.announcer", "event": "announce_tts_failed",
                       "error_code": error.code},
            )
            return False
        except Exception as error:
            LOGGER.warning(
                "알림을 말하지 못했습니다.",
                extra={"component": "voice.announcer", "event": "announce_failed",
                       "error_code": getattr(error, "code", type(error).__name__)},
            )
            return False

    def say_soon(self, text: str) -> None:
        """부르는 쪽을 붙잡지 않고 말한다. 앞말이 아직이면 이번 말은 건너뛴다."""

        if self._stopping:
            return
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self.say(text), name="voice-announce")

    async def stop(self) -> None:
        self._stopping = True
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
