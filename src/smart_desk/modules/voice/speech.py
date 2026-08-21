"""문장 하나를 그 자리에서 소리로 바꾼다.

평소 음성 turn은 마이크에서 시작해 SDK voice pipeline이 TTS까지 맡는다. 그런데
사용자를 알아본 순간의 인사처럼 먼저 말을 걸어야 하는 자리가 있고, 거기에는
마이크가 없다. 이 모듈은 그 자리를 위해 글을 받아 PCM으로 흘려준다.

내보내는 소리는 `PlaybackCoordinator.play_speech`가 그대로 스피커에 쓸 수 있는
규격, 즉 mono 16-bit PCM에 `OUTPUT_SAMPLE_RATE`다.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from smart_desk.modules.voice.models import OUTPUT_SAMPLE_RATE


LOGGER = logging.getLogger(__name__)

DEFAULT_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_TTS_VOICE = "nova"
# 인사말 정도의 길이만 허용한다. 길어지면 스피커를 오래 잡고 있게 된다.
MAX_TEXT_LENGTH = 400


class SpeechSynthesisError(RuntimeError):
    """소리를 만들지 못했다. code로 원인을 구분한다."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class OpenAiSpeechSynthesizer:
    """OpenAI TTS로 글을 PCM으로 바꾼다."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_TTS_MODEL,
        voice: str = DEFAULT_TTS_VOICE,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._client = client
        self._owns_client = client is None

    async def start(self) -> None:
        """Client는 첫 합성까지 만들지 않는다."""

    async def stop(self) -> None:
        """직접 만든 OpenAI client와 연결 pool을 닫는다."""
        if not self._owns_client:
            return
        client, self._client = self._client, None
        close = getattr(client, "close", None)
        if callable(close):
            await close()

    def _require_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise SpeechSynthesisError("tts_api_key_missing")
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=self._api_key)
        return self._client

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        """글을 읽어 PCM 조각을 차례로 내놓는다."""

        spoken = text.strip()
        if not spoken:
            raise SpeechSynthesisError("tts_text_empty")
        if len(spoken) > MAX_TEXT_LENGTH:
            raise SpeechSynthesisError("tts_text_too_long")

        client = self._require_client()
        try:
            async with client.audio.speech.with_streaming_response.create(
                model=self._model,
                voice=self._voice,
                input=spoken,
                response_format="pcm",
            ) as response:
                async for chunk in response.iter_bytes():
                    if chunk:
                        yield chunk
        except SpeechSynthesisError:
            raise
        except Exception as error:
            LOGGER.warning(
                "TTS 합성에 실패했습니다.",
                extra={
                    "component": "voice.speech",
                    "event": "tts_synthesis_failed",
                    "error": str(error),
                },
            )
            raise SpeechSynthesisError("tts_synthesis_failed") from error

    @property
    def sample_rate(self) -> int:
        """내보내는 PCM의 sample rate. 스피커 규격과 같아야 한다."""

        return OUTPUT_SAMPLE_RATE
