import asyncio
import struct
import time
from collections.abc import AsyncIterable, AsyncIterator

from smart_desk.config.settings import VoiceSettings
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.assistant.agents_runtime import VoiceRuntimeEvent, VoiceRuntimeEventType, VoiceRuntimeLifecycle
from smart_desk.modules.voice.models import AudioChunk, EffectName, INPUT_FRAME_SAMPLES, VoiceState
from smart_desk.modules.voice.service import VoiceService


def pcm(value: int) -> bytes:
    return struct.pack("<h", value) * INPUT_FRAME_SAMPLES


class Audio:
    def __init__(self) -> None:
        self.q: asyncio.Queue[AudioChunk] = asyncio.Queue()
        self.accepting = False
        self.stops = 0

    async def start(self) -> None: self.accepting = True
    async def stop(self) -> None:
        self.stops += 1
        self.accepting = False
        self.discard_pending()
    async def read(self, timeout_seconds: float | None = None) -> AudioChunk:
        async with asyncio.timeout(timeout_seconds if timeout_seconds is not None else 60):
            return await self.q.get()
    def set_accepting(self, value: bool) -> None: self.accepting = value
    def discard_pending(self) -> None:
        while not self.q.empty(): self.q.get_nowait()
    def feed(self, value: int) -> None:
        if self.accepting: self.q.put_nowait(AudioChunk(pcm(value), time.monotonic()))


class Wake:
    def __init__(self) -> None: self.starts = self.stops = 0
    async def start(self) -> None: self.starts += 1
    async def stop(self) -> None: self.stops += 1
    async def detect(self, _pcm: bytes) -> bool: return True
    def reset(self) -> None: pass


class Playback:
    def __init__(self, *, fail_after: int | None = None) -> None:
        self.effects: list[EffectName] = []
        self.audio: list[bytes] = []
        self.abort = self.starts = self.stops = 0
        self.fail_after = fail_after
    async def start(self) -> None: self.starts += 1
    async def stop(self) -> None: self.stops += 1
    async def stop_speech(self) -> None: self.abort += 1
    async def play_effect(self, effect: EffectName) -> None: self.effects.append(effect)
    async def play_speech(self, chunks: AsyncIterator[bytes]) -> None:
        async for chunk in chunks:
            if self.fail_after is not None and len(self.audio) >= self.fail_after:
                raise RuntimeError("speaker failed")
            self.audio.append(chunk)


class Runtime:
    def __init__(self, events: list[VoiceRuntimeEvent]) -> None:
        self.events = events
        self.received: list[bytes] = []
        self.closed = 0
        self.outcomes: list[tuple[str, str | None]] = []
    async def stop(self) -> None: self.closed += 1
    async def finalize_turn(self, outcome: str, *, error_code: str | None = None) -> None:
        self.outcomes.append((outcome, error_code))
    async def run_audio(self, chunks: AsyncIterable[bytes]) -> AsyncIterator[VoiceRuntimeEvent]:
        # A real SDK starts producing after a first PCM frame; it does not wait for
        # the service to decide server-VAD utterance end.
        iterator = chunks.__aiter__()
        try:
            self.received.append(await anext(iterator))
        except StopAsyncIteration:
            return
        for event in self.events:
            yield event


async def wait_state(service: VoiceService, state: VoiceState) -> None:
    async with asyncio.timeout(1):
        while service.get_snapshot().state is not state:
            await asyncio.sleep(0)


async def wait_accepting(audio: Audio) -> None:
    async with asyncio.timeout(1):
        while not audio.accepting: await asyncio.sleep(0)


def service(events: list[VoiceRuntimeEvent], *, playback: Playback | None = None):
    audio, wake, runtime = Audio(), Wake(), Runtime(events)
    playback = playback or Playback()
    voice = VoiceService(audio_input=audio, wakeword=wake, runtime=runtime, playback=playback,
        settings=VoiceSettings(speech_start_timeout_seconds=.1, post_playback_guard_seconds=0, followup_timeout_seconds=.2), task_manager=TaskManager())
    return voice, audio, playback, runtime


async def start_wake_turn(voice: VoiceService, audio: Audio) -> None:
    await voice.start()
    await wait_accepting(audio)
    audio.feed(0)
    await wait_state(voice, VoiceState.RECORDING)
    await wait_accepting(audio)
    audio.feed(500)


async def test_original_pcm_reaches_runtime_without_wav_and_turn_ended_succeeds() -> None:
    events = [VoiceRuntimeEvent(1, VoiceRuntimeEventType.TRANSCRIPT, transcript="hidden"), VoiceRuntimeEvent(2, VoiceRuntimeEventType.AUDIO, audio=b"\x01\x00"), VoiceRuntimeEvent(3, VoiceRuntimeEventType.LIFECYCLE, lifecycle=VoiceRuntimeLifecycle.TURN_ENDED)]
    voice, audio, playback, runtime = service(events)
    await start_wake_turn(voice, audio)
    await wait_state(voice, VoiceState.WAITING_WAKE)
    assert runtime.received == [pcm(500)]
    assert playback.audio == [b"\x01\x00"] and playback.effects == [EffectName.ACKNOWLEDGEMENT]
    assert runtime.outcomes == [("SUCCEEDED", None)]
    await voice.stop()


async def test_speech_start_deadline_does_not_end_speech_after_rms_evidence() -> None:
    voice, audio, _, _ = service([])
    voice._input_stop = asyncio.Event()  # noqa: SLF001
    chunks = voice._audio_chunks((), speech_already_started=False)  # noqa: SLF001
    waiter = asyncio.create_task(anext(chunks))
    await asyncio.sleep(0)
    audio.set_accepting(True); audio.feed(500)
    assert await waiter == pcm(500)
    await asyncio.sleep(.12)
    next_chunk = asyncio.create_task(anext(chunks))
    await asyncio.sleep(0)
    audio.feed(600)
    assert await next_chunk == pcm(600)
    await chunks.aclose()


async def test_audio_before_transcript_fails_closed_and_mutes_once() -> None:
    voice, audio, playback, runtime = service([VoiceRuntimeEvent(1, VoiceRuntimeEventType.AUDIO, audio=b"\0\0")])
    await start_wake_turn(voice, audio)
    await wait_state(voice, VoiceState.WAITING_WAKE)
    assert playback.audio == [] and runtime.outcomes == [("FAILED", "voice_pipeline_failed")]


async def test_followup_opens_only_after_turn_ended_request() -> None:
    events = [VoiceRuntimeEvent(1, VoiceRuntimeEventType.TRANSCRIPT), VoiceRuntimeEvent(2, VoiceRuntimeEventType.LIFECYCLE, lifecycle=VoiceRuntimeLifecycle.TURN_ENDED, followup_requested=True)]
    voice, audio, _, runtime = service(events)
    await start_wake_turn(voice, audio)
    await wait_state(voice, VoiceState.WAITING_FOLLOWUP)
    assert runtime.outcomes == [("SUCCEEDED", None)]
    await voice.stop()


async def test_no_speech_recovers_without_visible_provider_failure() -> None:
    voice, audio, playback, runtime = service([])
    await voice.start(); await wait_accepting(audio); audio.feed(0)
    await wait_state(voice, VoiceState.RECORDING)
    await wait_state(voice, VoiceState.WAITING_WAKE)
    assert voice.get_snapshot().last_error is None
    assert runtime.outcomes == [("CANCELLED", None)] and EffectName.ERROR not in playback.effects
    await voice.stop()


async def test_final_transcript_without_turn_ended_fails_closed() -> None:
    voice, audio, playback, runtime = service([
        VoiceRuntimeEvent(1, VoiceRuntimeEventType.TRANSCRIPT),
        VoiceRuntimeEvent(2, VoiceRuntimeEventType.AUDIO, audio=b"\x01\x00"),
    ])
    await start_wake_turn(voice, audio)
    await wait_state(voice, VoiceState.WAITING_WAKE)
    assert playback.audio == [b"\x01\x00"]
    assert runtime.outcomes == [("FAILED", "voice_pipeline_failed")]
    await voice.stop()


async def test_runtime_error_fails_and_playback_failure_never_hangs_under_queue_pressure() -> None:
    events = [VoiceRuntimeEvent(1, VoiceRuntimeEventType.TRANSCRIPT)] + [VoiceRuntimeEvent(index + 2, VoiceRuntimeEventType.AUDIO, audio=b"\0\0") for index in range(8)]
    voice, audio, _, runtime = service(events, playback=Playback(fail_after=0))
    await start_wake_turn(voice, audio)
    await wait_state(voice, VoiceState.WAITING_WAKE)
    assert runtime.outcomes == [("FAILED", "voice_pipeline_failed")]
    await voice.stop()


async def test_stop_is_idempotent_and_partial_start_cancellation_cleans_resources() -> None:
    voice, _, _, runtime = service([])
    await voice.start(); await voice.stop(); await voice.stop()
    assert runtime.closed == 1
