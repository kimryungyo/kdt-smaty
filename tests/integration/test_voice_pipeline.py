"""Provider-neutral wake → runtime event → playback integration contract."""

import asyncio

from tests.unit.test_voice_service import service, wait_state
from smart_desk.modules.assistant.agents_runtime import VoiceRuntimeEvent, VoiceRuntimeEventType, VoiceRuntimeLifecycle
from smart_desk.modules.voice.models import VoiceState


async def test_fake_sdk_pipeline_wake_transcript_audio_and_no_followup():
    events = [
        VoiceRuntimeEvent(1, VoiceRuntimeEventType.TRANSCRIPT),
        VoiceRuntimeEvent(2, VoiceRuntimeEventType.AUDIO, audio=b"\x01\x00"),
        VoiceRuntimeEvent(3, VoiceRuntimeEventType.LIFECYCLE, lifecycle=VoiceRuntimeLifecycle.TURN_ENDED, followup_requested=False),
    ]
    voice, audio, playback, _ = service(events)
    await voice.start(); audio.feed(0)
    await wait_state(voice, VoiceState.RECORDING)
    audio.feed(2000)
    await wait_state(voice, VoiceState.WAITING_WAKE)
    assert playback.audio == [b"\x01\x00"]
    await voice.stop()


async def test_fake_sdk_pipeline_opens_followup_only_when_turn_requests_it():
    events = [
        VoiceRuntimeEvent(1, VoiceRuntimeEventType.TRANSCRIPT),
        VoiceRuntimeEvent(2, VoiceRuntimeEventType.AUDIO, audio=b"\x01\x00"),
        VoiceRuntimeEvent(3, VoiceRuntimeEventType.LIFECYCLE, lifecycle=VoiceRuntimeLifecycle.TURN_ENDED, followup_requested=True),
    ]
    voice, audio, _, _ = service(events)
    await voice.start()
    while not audio.accepting:
        await asyncio.sleep(0)
    audio.feed(0)
    await wait_state(voice, VoiceState.RECORDING)
    while not audio.accepting:
        await asyncio.sleep(0)
    audio.feed(2000)
    await wait_state(voice, VoiceState.WAITING_FOLLOWUP)
    await voice.stop()
