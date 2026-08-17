import asyncio
import struct
import time
from collections.abc import AsyncIterable, AsyncIterator

from smart_desk.config.settings import VoiceSettings
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.assistant.agents_runtime import VoiceRuntimeEvent, VoiceRuntimeEventType, VoiceRuntimeLifecycle
from smart_desk.modules.voice.models import AudioChunk, EffectName, INPUT_FRAME_SAMPLES, VoiceState
from smart_desk.modules.voice.service import VoiceService


def pcm(value: int) -> bytes: return struct.pack("<h", value) * INPUT_FRAME_SAMPLES

class Audio:
    def __init__(self): self.q = asyncio.Queue(); self.accepting = False; self.stops = 0
    async def start(self): self.accepting = True
    async def stop(self): self.stops += 1; self.accepting = False; self.discard_pending()
    async def read(self, timeout_seconds=None):
        async with asyncio.timeout(timeout_seconds) if timeout_seconds else asyncio.timeout(60): return await self.q.get()
    def set_accepting(self, value): self.accepting = value
    def discard_pending(self):
        while not self.q.empty(): self.q.get_nowait()
    def feed(self, value):
        if self.accepting: self.q.put_nowait(AudioChunk(pcm(value), time.monotonic()))

class Wake:
    async def start(self): pass
    async def stop(self): pass
    async def detect(self, _): return True
    def reset(self): pass

class Playback:
    def __init__(self): self.effects=[]; self.audio=[]; self.abort=0
    async def start(self): pass
    async def stop(self): pass
    async def stop_speech(self): self.abort += 1
    async def play_effect(self, effect): self.effects.append(effect)
    async def play_speech(self, chunks):
        async for chunk in chunks: self.audio.append(chunk)

class Runtime:
    def __init__(self, events): self.events=events; self.received=[]; self.closed=0
    async def stop(self): self.closed += 1
    async def run_audio(self, chunks: AsyncIterable[bytes]) -> AsyncIterator[VoiceRuntimeEvent]:
        async for chunk in chunks: self.received.append(chunk)
        for event in self.events: yield event

async def wait_state(service, state):
    async with asyncio.timeout(1):
        while service.get_snapshot().state is not state: await asyncio.sleep(0)

def service(events):
    audio=Audio(); playback=Playback(); runtime=Runtime(events)
    return VoiceService(audio_input=audio, wakeword=Wake(), runtime=runtime, playback=playback,
      settings=VoiceSettings(speech_start_timeout_seconds=.2, post_playback_guard_seconds=0, followup_timeout_seconds=.2), task_manager=TaskManager()), audio, playback, runtime

async def test_original_pcm_reaches_runtime_then_transcript_and_audio_return_to_wake():
    events=[VoiceRuntimeEvent(1, VoiceRuntimeEventType.TRANSCRIPT, transcript="hidden"), VoiceRuntimeEvent(2, VoiceRuntimeEventType.AUDIO, audio=b"\x01\x00"), VoiceRuntimeEvent(3, VoiceRuntimeEventType.LIFECYCLE, lifecycle=VoiceRuntimeLifecycle.TURN_ENDED)]
    voice,audio,playback,runtime=service(events); await voice.start(); await wait_state(voice, VoiceState.WAITING_WAKE)
    source=pcm(400); audio.feed(0); await asyncio.sleep(0); audio.feed(400)
    await wait_state(voice, VoiceState.WAITING_WAKE)
    assert runtime.received == [source]
    assert playback.audio == [b"\x01\x00"] and playback.effects == [EffectName.ACKNOWLEDGEMENT]
    await voice.stop(); assert runtime.closed == 1

async def test_turn_ended_followup_only_opens_when_requested():
    events=[VoiceRuntimeEvent(1, VoiceRuntimeEventType.TRANSCRIPT), VoiceRuntimeEvent(2, VoiceRuntimeEventType.LIFECYCLE, lifecycle=VoiceRuntimeLifecycle.TURN_ENDED, followup_requested=True)]
    voice,audio,_,_=service(events); await voice.start(); audio.feed(0); await asyncio.sleep(0); audio.feed(500)
    await wait_state(voice, VoiceState.WAITING_FOLLOWUP); await voice.stop()

async def test_error_recovers_and_stop_is_idempotent():
    events=[VoiceRuntimeEvent(1, VoiceRuntimeEventType.ERROR, error_code="provider-secret")]
    voice,audio,playback,runtime=service(events); await voice.start(); audio.feed(0); await asyncio.sleep(0); audio.feed(500)
    await wait_state(voice, VoiceState.WAITING_WAKE)
    assert voice.get_snapshot().last_error == "voice_pipeline_failed" and EffectName.ERROR in playback.effects
    await voice.stop(); await voice.stop(); assert runtime.closed == 1
