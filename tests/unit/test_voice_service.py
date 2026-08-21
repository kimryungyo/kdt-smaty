import asyncio
import struct
import time
from collections.abc import AsyncIterable, AsyncIterator

from smart_desk.config.settings import VoiceSettings
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.assistant.agents_runtime import VoiceRuntimeEvent, VoiceRuntimeEventType, VoiceRuntimeLifecycle
from smart_desk.modules.voice.models import AudioChunk, EffectName, INPUT_FRAME_SAMPLES, VoiceFatalError, VoiceState
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


class BlockingAcknowledgementPlayback(Playback):
    def __init__(self) -> None:
        super().__init__()
        self.ack_started = asyncio.Event()
        self.release_ack = asyncio.Event()

    async def play_effect(self, effect: EffectName) -> None:
        self.effects.append(effect)
        if effect is EffectName.ACKNOWLEDGEMENT:
            self.ack_started.set()
            await self.release_ack.wait()


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


class HangingRuntime(Runtime):
    def __init__(self, events: list[VoiceRuntimeEvent]) -> None:
        super().__init__(events)
        self.release = asyncio.Event()

    async def run_audio(self, chunks: AsyncIterable[bytes]) -> AsyncIterator[VoiceRuntimeEvent]:
        iterator = chunks.__aiter__()
        try:
            self.received.append(await anext(iterator))
        except StopAsyncIteration:
            return
        for event in self.events:
            yield event
        await self.release.wait()
        if False:
            yield VoiceRuntimeEvent(0, VoiceRuntimeEventType.ERROR)


class StuckOnEmptyInputRuntime(Runtime):
    """Reproduce Agents SDK 0.21 STT waiting after the input sentinel."""

    def __init__(self) -> None:
        super().__init__([])
        self.release = asyncio.Event()

    async def run_audio(self, chunks: AsyncIterable[bytes]) -> AsyncIterator[VoiceRuntimeEvent]:
        iterator = chunks.__aiter__()
        try:
            self.received.append(await anext(iterator))
        except StopAsyncIteration:
            await self.release.wait()
        if False:
            yield VoiceRuntimeEvent(0, VoiceRuntimeEventType.ERROR)


class SlowTranscriptRuntime(Runtime):
    """응답 오디오가 먼저 흐르고 입력 전사가 녹음 제한보다 늦게 도착한다."""

    def __init__(self) -> None:
        super().__init__([])
        self.release = asyncio.Event()

    async def run_audio(self, chunks: AsyncIterable[bytes]) -> AsyncIterator[VoiceRuntimeEvent]:
        iterator = chunks.__aiter__()
        try:
            self.received.append(await anext(iterator))
        except StopAsyncIteration:
            return
        yield VoiceRuntimeEvent(1, VoiceRuntimeEventType.AUDIO, audio=b"\x01\x00")
        await self.release.wait()
        yield VoiceRuntimeEvent(2, VoiceRuntimeEventType.TRANSCRIPT, transcript="늦은 전사")
        yield VoiceRuntimeEvent(3, VoiceRuntimeEventType.LIFECYCLE,
                                lifecycle=VoiceRuntimeLifecycle.TURN_ENDED)


class ProcessingRuntime(Runtime):
    def __init__(self) -> None:
        super().__init__([])
        self.processing = asyncio.Event()
        self.release = asyncio.Event()

    async def run_audio(self, chunks: AsyncIterable[bytes]) -> AsyncIterator[VoiceRuntimeEvent]:
        iterator = chunks.__aiter__()
        self.received.append(await anext(iterator))
        yield VoiceRuntimeEvent(
            1,
            VoiceRuntimeEventType.LIFECYCLE,
            lifecycle=VoiceRuntimeLifecycle.SPEECH_STARTED,
        )
        yield VoiceRuntimeEvent(
            2,
            VoiceRuntimeEventType.LIFECYCLE,
            lifecycle=VoiceRuntimeLifecycle.PROCESSING_STARTED,
        )
        self.processing.set()
        await self.release.wait()
        yield VoiceRuntimeEvent(3, VoiceRuntimeEventType.TRANSCRIPT, transcript="처리 중")
        yield VoiceRuntimeEvent(4, VoiceRuntimeEventType.AUDIO, audio=b"\x01\x00")
        yield VoiceRuntimeEvent(
            5,
            VoiceRuntimeEventType.LIFECYCLE,
            lifecycle=VoiceRuntimeLifecycle.TURN_ENDED,
        )


class ClosableErrorRuntime(Runtime):
    def __init__(self) -> None:
        super().__init__([])
        self.closed_stream = asyncio.Event()

    async def run_audio(self, chunks: AsyncIterable[bytes]) -> AsyncIterator[VoiceRuntimeEvent]:
        iterator = chunks.__aiter__()
        self.received.append(await anext(iterator))
        try:
            yield VoiceRuntimeEvent(
                1,
                VoiceRuntimeEventType.ERROR,
                error_code="voice_pipeline_failed",
            )
        finally:
            self.closed_stream.set()


async def wait_state(service: VoiceService, state: VoiceState) -> None:
    async with asyncio.timeout(1):
        while service.get_snapshot().state is not state:
            await asyncio.sleep(0)


async def wait_reopen(playback: "Playback", opens: int) -> None:
    async with asyncio.timeout(1):
        while playback.opens < opens: await asyncio.sleep(0)


async def wait_accepting(audio: Audio) -> None:
    async with asyncio.timeout(1):
        while not audio.accepting: await asyncio.sleep(0)


def service(
    events: list[VoiceRuntimeEvent],
    *,
    playback: Playback | None = None,
    runtime: Runtime | None = None,
    settings: VoiceSettings | None = None,
):
    audio, wake = Audio(), Wake()
    runtime = runtime or Runtime(events)
    playback = playback or Playback()
    voice = VoiceService(audio_input=audio, wakeword=wake, runtime=runtime, playback=playback,
        settings=settings or VoiceSettings(speech_start_timeout_seconds=.1, post_playback_guard_seconds=0, followup_timeout_seconds=.2), task_manager=TaskManager())
    return voice, audio, playback, runtime


async def start_wake_turn(voice: VoiceService, audio: Audio) -> None:
    await voice.start()
    await wait_accepting(audio)
    audio.feed(0)
    await wait_state(voice, VoiceState.RECORDING)
    await wait_accepting(audio)
    audio.feed(500)


async def test_acknowledgement_is_not_reported_as_recording() -> None:
    playback = BlockingAcknowledgementPlayback()
    voice, audio, _, _ = service([], playback=playback)
    await voice.start()
    await wait_accepting(audio)
    audio.feed(0)
    await playback.ack_started.wait()

    assert voice.get_snapshot().state is VoiceState.ACKNOWLEDGING
    assert audio.accepting is False
    assert voice.trigger_wake() is False

    playback.release_ack.set()
    await wait_state(voice, VoiceState.RECORDING)
    assert audio.accepting is True
    await voice.stop()


async def test_provider_vad_moves_recording_to_processing_before_transcript() -> None:
    runtime = ProcessingRuntime()
    voice, audio, _, _ = service([], runtime=runtime)
    await start_wake_turn(voice, audio)
    await runtime.processing.wait()

    assert voice.get_snapshot().state is VoiceState.PROCESSING
    assert audio.accepting is False

    runtime.release.set()
    await wait_state(voice, VoiceState.WAITING_WAKE)
    await voice.stop()


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
    # 임계값을 넘는 프레임이 발화 시작 증거가 된다.
    audio.set_accepting(True); audio.feed(2000)
    assert await waiter == pcm(2000)
    await asyncio.sleep(.12)
    next_chunk = asyncio.create_task(anext(chunks))
    await asyncio.sleep(0)
    audio.feed(600)
    assert await next_chunk == pcm(600)
    await chunks.aclose()


async def test_provider_speech_started_keeps_quiet_input_open() -> None:
    voice, audio, _, _ = service([])
    voice._input_stop = asyncio.Event()  # noqa: SLF001
    provider_speech_started = asyncio.Event()
    chunks = voice._audio_chunks(  # noqa: SLF001
        (),
        speech_already_started=False,
        speech_started_event=provider_speech_started,
    )
    first = asyncio.create_task(anext(chunks))
    await asyncio.sleep(0)
    audio.set_accepting(True)
    audio.feed(500)
    assert await first == pcm(500)

    provider_speech_started.set()
    await asyncio.sleep(.12)
    following = asyncio.create_task(anext(chunks))
    await asyncio.sleep(0)
    audio.feed(500)
    assert await following == pcm(500)
    await chunks.aclose()


async def test_audio_before_transcript_plays_and_finishes_after_late_transcript() -> None:
    voice, audio, playback, runtime = service([
        VoiceRuntimeEvent(1, VoiceRuntimeEventType.AUDIO, audio=b"\0\0"),
        VoiceRuntimeEvent(2, VoiceRuntimeEventType.TRANSCRIPT, transcript="늦은 전사"),
        VoiceRuntimeEvent(
            3,
            VoiceRuntimeEventType.LIFECYCLE,
            lifecycle=VoiceRuntimeLifecycle.TURN_ENDED,
        ),
    ])
    await start_wake_turn(voice, audio)
    await wait_state(voice, VoiceState.WAITING_WAKE)
    assert playback.audio == [b"\0\0"]
    assert runtime.outcomes == [("SUCCEEDED", None)]
    await voice.stop()


async def test_followup_opens_only_after_turn_ended_request() -> None:
    events = [VoiceRuntimeEvent(1, VoiceRuntimeEventType.TRANSCRIPT), VoiceRuntimeEvent(2, VoiceRuntimeEventType.AUDIO, audio=b"\x01\x00"), VoiceRuntimeEvent(3, VoiceRuntimeEventType.LIFECYCLE, lifecycle=VoiceRuntimeLifecycle.TURN_ENDED, followup_requested=True)]
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


async def test_no_speech_cancels_sdk_stream_that_stays_open_after_input_ends() -> None:
    runtime = StuckOnEmptyInputRuntime()
    settings = VoiceSettings(
        speech_start_timeout_seconds=.01,
        recording_timeout_seconds=.1,
        turn_timeout_seconds=.2,
        post_playback_guard_seconds=0,
        followup_timeout_seconds=.2,
    )
    voice, audio, playback, _ = service([], runtime=runtime, settings=settings)

    await voice.start()
    await wait_accepting(audio)
    audio.feed(0)
    await wait_state(voice, VoiceState.RECORDING)
    await wait_state(voice, VoiceState.WAITING_WAKE)

    assert runtime.outcomes == [("CANCELLED", None)]
    assert EffectName.ERROR not in playback.effects
    assert voice.get_snapshot().last_error is None
    await voice.stop()


async def test_recording_timeout_cancels_stalled_stt_and_rearms_wakeword() -> None:
    runtime = HangingRuntime([])
    settings = VoiceSettings(
        speech_start_timeout_seconds=.01,
        recording_timeout_seconds=.05,
        turn_timeout_seconds=.2,
        post_playback_guard_seconds=0,
        followup_timeout_seconds=.2,
    )
    voice, audio, playback, _ = service([], runtime=runtime, settings=settings)

    await start_wake_turn(voice, audio)
    await wait_state(voice, VoiceState.WAITING_WAKE)

    assert voice.get_snapshot().last_error == "voice_recording_timeout"
    assert runtime.outcomes == [("FAILED", "voice_recording_timeout")]
    assert playback.effects[-1] is EffectName.ERROR
    assert audio.accepting is True
    await voice.stop()


async def test_turn_timeout_cancels_stalled_response_and_rearms_wakeword() -> None:
    runtime = HangingRuntime(
        [VoiceRuntimeEvent(1, VoiceRuntimeEventType.TRANSCRIPT, transcript="확정")]
    )
    settings = VoiceSettings(
        speech_start_timeout_seconds=.01,
        recording_timeout_seconds=.05,
        turn_timeout_seconds=.08,
        post_playback_guard_seconds=0,
        followup_timeout_seconds=.2,
    )
    voice, audio, playback, _ = service([], runtime=runtime, settings=settings)

    await start_wake_turn(voice, audio)
    await wait_state(voice, VoiceState.WAITING_WAKE)

    assert voice.get_snapshot().last_error == "voice_turn_timeout"
    assert runtime.outcomes == [("FAILED", "voice_turn_timeout")]
    assert playback.effects[-1] is EffectName.ERROR
    assert audio.accepting is True
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


async def test_turn_ended_without_response_audio_is_not_marked_successful() -> None:
    voice, audio, playback, runtime = service([
        VoiceRuntimeEvent(1, VoiceRuntimeEventType.TRANSCRIPT, transcript="대답해줘"),
        VoiceRuntimeEvent(
            2,
            VoiceRuntimeEventType.LIFECYCLE,
            lifecycle=VoiceRuntimeLifecycle.TURN_ENDED,
        ),
    ])
    await start_wake_turn(voice, audio)
    await wait_state(voice, VoiceState.WAITING_WAKE)

    assert runtime.outcomes == [("FAILED", "voice_response_audio_missing")]
    assert playback.effects[-1] is EffectName.ERROR
    assert voice.get_snapshot().last_error == "voice_response_audio_missing"
    await voice.stop()


async def test_runtime_error_fails_and_playback_failure_never_hangs_under_queue_pressure() -> None:
    events = [VoiceRuntimeEvent(1, VoiceRuntimeEventType.TRANSCRIPT)] + [VoiceRuntimeEvent(index + 2, VoiceRuntimeEventType.AUDIO, audio=b"\0\0") for index in range(8)]
    voice, audio, _, runtime = service(events, playback=Playback(fail_after=0))
    await start_wake_turn(voice, audio)
    await wait_state(voice, VoiceState.WAITING_WAKE)
    assert runtime.outcomes == [("FAILED", "voice_pipeline_failed")]
    await voice.stop()


async def test_runtime_stream_is_closed_immediately_after_error_event() -> None:
    runtime = ClosableErrorRuntime()
    voice, audio, _, _ = service([], runtime=runtime)

    await start_wake_turn(voice, audio)
    await wait_state(voice, VoiceState.WAITING_WAKE)

    assert runtime.closed_stream.is_set()
    await voice.stop()


async def test_trigger_wake_starts_turn_like_real_detection() -> None:
    voice, audio, playback, runtime = service([])
    await voice.start(); await wait_accepting(audio)
    assert voice.get_snapshot().state is VoiceState.WAITING_WAKE
    # 마이크에 아무것도 넣지 않아도 수동 트리거만으로 turn이 열린다.
    assert voice.trigger_wake() is True
    await wait_state(voice, VoiceState.RECORDING)
    assert EffectName.ACKNOWLEDGEMENT in playback.effects
    await wait_state(voice, VoiceState.WAITING_WAKE)
    await voice.stop()


async def test_trigger_wake_ignored_outside_waiting_wake() -> None:
    voice, audio, _, _ = service([])
    assert voice.trigger_wake() is False  # 시작 전 DISABLED
    await voice.start(); await wait_accepting(audio)
    assert voice.trigger_wake() is True
    await wait_state(voice, VoiceState.RECORDING)
    assert voice.trigger_wake() is False  # turn이 도는 중이면 무시
    await voice.stop()


async def test_stop_is_idempotent_and_partial_start_cancellation_cleans_resources() -> None:
    voice, _, _, runtime = service([])
    await voice.start(); await voice.stop(); await voice.stop()
    assert runtime.closed == 1


class FatalSpeechPlayback(Playback):
    async def play_speech(self, chunks: AsyncIterator[bytes]) -> None:
        async for _chunk in chunks:
            raise VoiceFatalError("speaker_failed")


class DeadSpeaker(Playback):
    """USB가 다시 연결되면서 열어 둔 stream이 죽은 스피커."""

    def __init__(self, *, heal_after: int) -> None:
        super().__init__()
        self.heal_after = heal_after
        self.opens = 0

    async def start(self) -> None:
        await super().start()
        self.opens += 1

    async def play_effect(self, effect: EffectName) -> None:
        if self.opens <= self.heal_after:
            raise VoiceFatalError("speaker_failed")
        await super().play_effect(effect)


class DeadAnnouncementSpeaker(DeadSpeaker):
    async def play_speech(self, chunks: AsyncIterator[bytes]) -> None:
        if self.opens <= self.heal_after:
            raise VoiceFatalError("speaker_failed")
        await super().play_speech(chunks)


async def test_dead_speaker_during_wake_effect_reopens_device_instead_of_stopping(monkeypatch) -> None:
    monkeypatch.setattr("smart_desk.modules.voice.service.DEVICE_RETRY_INTERVAL_SECONDS", 0.01)
    events = [VoiceRuntimeEvent(1, VoiceRuntimeEventType.TRANSCRIPT), VoiceRuntimeEvent(2, VoiceRuntimeEventType.AUDIO, audio=b"\x01\x00"), VoiceRuntimeEvent(3, VoiceRuntimeEventType.LIFECYCLE, lifecycle=VoiceRuntimeLifecycle.TURN_ENDED)]
    playback = DeadSpeaker(heal_after=1)
    voice, audio, _, runtime = service(events, playback=playback)
    await voice.start()
    await wait_accepting(audio)
    audio.feed(0)
    # 첫 turn은 깨움 효과음에서 죽지만, 서비스가 멈추는 대신 장치를 다시 연다.
    await wait_state(voice, VoiceState.ERROR)
    await wait_reopen(playback, 2)
    await wait_state(voice, VoiceState.WAITING_WAKE)
    await wait_accepting(audio)
    audio.feed(0)
    await wait_state(voice, VoiceState.RECORDING)
    await wait_accepting(audio)
    audio.feed(500)
    await wait_state(voice, VoiceState.WAITING_WAKE)
    assert playback.audio == [b"\x01\x00"]
    assert runtime.outcomes[-1] == ("SUCCEEDED", None)
    await voice.stop()


async def test_speaker_loss_inside_turn_reaches_device_retry(monkeypatch) -> None:
    monkeypatch.setattr("smart_desk.modules.voice.service.DEVICE_RETRY_INTERVAL_SECONDS", 0.01)
    events = [VoiceRuntimeEvent(1, VoiceRuntimeEventType.TRANSCRIPT), VoiceRuntimeEvent(2, VoiceRuntimeEventType.AUDIO, audio=b"\x01\x00")]
    playback = FatalSpeechPlayback()
    voice, audio, _, runtime = service(events, playback=playback)
    await start_wake_turn(voice, audio)
    await wait_state(voice, VoiceState.WAITING_WAKE)
    # turn은 실패로 마감되고, 같은 stream으로 되돌아가는 대신 장치를 다시 연다.
    assert runtime.outcomes == [("FAILED", "speaker_failed")]
    assert playback.starts >= 2
    await voice.stop()


async def test_speaker_loss_during_announcement_reopens_device(monkeypatch) -> None:
    monkeypatch.setattr(
        "smart_desk.modules.voice.service.DEVICE_RETRY_INTERVAL_SECONDS", 0.01
    )
    playback = DeadAnnouncementSpeaker(heal_after=1)
    voice, audio, _, _ = service([], playback=playback)
    await voice.start()
    await wait_state(voice, VoiceState.WAITING_WAKE)

    async def spoken():
        yield b"\x01\x00"

    assert await voice.announce(spoken()) is False
    assert voice.get_snapshot().state is VoiceState.ERROR
    # main loop의 이미 대기 중인 read를 깨워도 늦은 wake로 turn이 열리면 안 된다.
    audio.q.put_nowait(AudioChunk(pcm(500), time.monotonic()))
    await wait_reopen(playback, 2)
    await wait_state(voice, VoiceState.WAITING_WAKE)
    assert await voice.announce(spoken()) is True
    await voice.stop()


async def test_inflight_wake_detection_cannot_open_turn_after_announcement() -> None:
    class SlowWake(Wake):
        def __init__(self) -> None:
            super().__init__()
            self.detecting = asyncio.Event()
            self.release = asyncio.Event()

        async def detect(self, _pcm: bytes) -> bool:
            self.detecting.set()
            await self.release.wait()
            return True

    audio, wake, playback, runtime = Audio(), SlowWake(), Playback(), Runtime([])
    voice = VoiceService(
        audio_input=audio,
        wakeword=wake,
        runtime=runtime,
        playback=playback,
        settings=VoiceSettings(
            speech_start_timeout_seconds=.1,
            post_playback_guard_seconds=0,
            followup_timeout_seconds=.2,
        ),
        task_manager=TaskManager(),
    )
    await voice.start()
    audio.feed(500)
    await wake.detecting.wait()

    async def spoken():
        yield b"\x01\x00"

    assert await voice.announce(spoken()) is True
    wake.release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert voice.get_snapshot().state is VoiceState.WAITING_WAKE
    assert playback.effects == []
    await voice.stop()


async def test_followup_opens_without_request_and_expires_into_waiting_wake() -> None:
    """AI가 request_followup을 부르지 않아도 창이 열리고, 발화가 없으면 대기로 돌아간다."""
    events = [
        VoiceRuntimeEvent(1, VoiceRuntimeEventType.TRANSCRIPT),
        VoiceRuntimeEvent(2, VoiceRuntimeEventType.AUDIO, audio=b"\x01\x00"),
        VoiceRuntimeEvent(3, VoiceRuntimeEventType.LIFECYCLE, lifecycle=VoiceRuntimeLifecycle.TURN_ENDED),
    ]
    voice, audio, _, runtime = service(events)
    await start_wake_turn(voice, audio)
    await wait_state(voice, VoiceState.WAITING_FOLLOWUP)
    assert runtime.outcomes == [("SUCCEEDED", None)]
    # 창 안에 발화가 없으면 조용히 웨이크 대기로 복귀한다.
    await wait_state(voice, VoiceState.WAITING_WAKE)
    assert voice.get_snapshot().last_error is None
    await voice.stop()


async def test_followup_disabled_returns_straight_to_waiting_wake() -> None:
    """followup_enabled=False면 창을 열지 않고 곧바로 웨이크 대기로 돌아간다."""
    events = [
        VoiceRuntimeEvent(1, VoiceRuntimeEventType.TRANSCRIPT),
        VoiceRuntimeEvent(2, VoiceRuntimeEventType.AUDIO, audio=b"\x01\x00"),
        VoiceRuntimeEvent(3, VoiceRuntimeEventType.LIFECYCLE, lifecycle=VoiceRuntimeLifecycle.TURN_ENDED),
    ]
    voice, audio, _, _ = service(events, settings=VoiceSettings(
        speech_start_timeout_seconds=.1, post_playback_guard_seconds=0,
        followup_timeout_seconds=.2, followup_enabled=False))
    await start_wake_turn(voice, audio)
    await wait_state(voice, VoiceState.WAITING_WAKE)
    assert voice.get_snapshot().state is VoiceState.WAITING_WAKE
    await voice.stop()


async def test_followup_ignores_single_frame_noise_spike() -> None:
    """단일 프레임 잡음 스파이크로는 follow-up turn이 열리지 않는다."""
    events = [
        VoiceRuntimeEvent(1, VoiceRuntimeEventType.TRANSCRIPT),
        VoiceRuntimeEvent(2, VoiceRuntimeEventType.AUDIO, audio=b"\x01\x00"),
        VoiceRuntimeEvent(3, VoiceRuntimeEventType.LIFECYCLE, lifecycle=VoiceRuntimeLifecycle.TURN_ENDED),
    ]
    voice, audio, _, _ = service(events, settings=VoiceSettings(
        speech_start_timeout_seconds=.1, post_playback_guard_seconds=0,
        followup_timeout_seconds=.5, followup_speech_frames=3))
    await start_wake_turn(voice, audio)
    await wait_state(voice, VoiceState.WAITING_FOLLOWUP)
    # 잡음 스파이크 한 프레임 뒤 무음이 이어지면 streak가 끊겨 창이 유지된다.
    for _ in range(6):
        audio.feed(5000); audio.feed(0)
        await asyncio.sleep(0)
    assert voice.get_snapshot().state is VoiceState.WAITING_FOLLOWUP
    # 창이 만료되면 조용히 웨이크 대기로 돌아간다.
    await wait_state(voice, VoiceState.WAITING_WAKE)
    await voice.stop()


async def test_followup_opens_on_sustained_speech() -> None:
    """연속 프레임이 임계값을 넘으면 follow-up turn이 열린다."""
    events = [
        VoiceRuntimeEvent(1, VoiceRuntimeEventType.TRANSCRIPT),
        VoiceRuntimeEvent(2, VoiceRuntimeEventType.AUDIO, audio=b"\x01\x00"),
        VoiceRuntimeEvent(3, VoiceRuntimeEventType.LIFECYCLE, lifecycle=VoiceRuntimeLifecycle.TURN_ENDED),
    ]
    voice, audio, _, _ = service(events, settings=VoiceSettings(
        speech_start_timeout_seconds=.1, post_playback_guard_seconds=0,
        followup_timeout_seconds=2, followup_speech_frames=3))
    await start_wake_turn(voice, audio)
    await wait_state(voice, VoiceState.WAITING_FOLLOWUP)
    for _ in range(3):
        audio.feed(5000)
    await wait_state(voice, VoiceState.RECORDING)
    await voice.stop()


async def test_recording_timeout_does_not_cut_response_already_playing() -> None:
    """응답 재생이 시작된 뒤에는 입력 전사가 늦어도 녹음 제한이 turn을 끊지 않는다."""
    events = [
        VoiceRuntimeEvent(1, VoiceRuntimeEventType.AUDIO, audio=b"\x01\x00"),
        VoiceRuntimeEvent(2, VoiceRuntimeEventType.TRANSCRIPT, transcript="늦은 전사"),
        VoiceRuntimeEvent(3, VoiceRuntimeEventType.LIFECYCLE, lifecycle=VoiceRuntimeLifecycle.TURN_ENDED),
    ]
    runtime = SlowTranscriptRuntime()
    voice, audio, playback, _ = service(events, runtime=runtime, settings=VoiceSettings(
        speech_start_timeout_seconds=.1, post_playback_guard_seconds=0,
        followup_timeout_seconds=.2, recording_timeout_seconds=.3))
    await start_wake_turn(voice, audio)
    await wait_state(voice, VoiceState.SPEAKING)
    # 녹음 제한(0.3초)을 넘겨도 재생 중인 응답은 살아 있어야 한다.
    await asyncio.sleep(.5)
    assert voice.get_snapshot().last_error != "voice_recording_timeout"
    runtime.release.set()
    await voice.stop()
