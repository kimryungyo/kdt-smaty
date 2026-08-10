"""명시적으로 opt-in한 환경에서만 실행하는 OpenAI Voice live smoke test."""

from pathlib import Path
import os
from io import BytesIO
import wave

import pytest

from smart_desk.config.settings import Settings
from smart_desk.modules.assistant.openai import OpenAiGateway
from smart_desk.modules.assistant.service import AssistantService
from smart_desk.modules.voice.models import AudioUtterance


pytestmark = pytest.mark.openai_voice_integration


async def test_openai_stt_two_responses_turns_and_streaming_tts() -> None:
    fixture_path = Path(os.environ["SMART_DESK_OPENAI_VOICE_FIXTURE"])
    wav = fixture_path.read_bytes()
    with wave.open(BytesIO(wav), "rb") as wav_file:
        duration = wav_file.getnframes() / wav_file.getframerate()
    settings = Settings(_env_file=None)
    gateway = OpenAiGateway(settings.openai)
    assistant = AssistantService(gateway, session_max_turns=12)

    try:
        transcript = await gateway.transcribe(
            AudioUtterance(wav=wav, duration_seconds=duration)
        )
        assert transcript.strip()
        first = await assistant.reply(transcript)
        second = await assistant.reply("방금 질문의 주제를 짧게 다시 말해 주세요.")
        assert first.spoken_text
        assert second.spoken_text
        assert assistant._session.completed_turns == 2  # noqa: SLF001

        stream = gateway.synthesize(second.spoken_text)
        first_chunk = await anext(stream)
        assert first_chunk
        await stream.aclose()
    finally:
        await gateway.close()
