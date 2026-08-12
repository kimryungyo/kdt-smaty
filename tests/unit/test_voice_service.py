"""VoiceService 상태 전이, 오류와 lifecycle 테스트."""

import asyncio
from collections import deque
import struct

import pytest

from smart_desk.config.settings import VoiceSettings
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.assistant.models import AssistantReply
from smart_desk.modules.assistant.openai import OpenAiTurnError
from smart_desk.modules.voice.audio import build_wav
from smart_desk.modules.voice.models import (
    AudioChunk,
    EffectName,
    RecordingEnd,
    VoiceFatalError,
    VoiceState,
)
from smart_desk.modules.voice.service import VoiceService


def pcm_frame(value: int) -> bytes:
    return struct.pack("<h", value) * 1_280


class FakeAudioInput:
    def __init__(self, events: list[str] | None = None) -> None:
        self.queue: asyncio.Queue[AudioChunk] = asyncio.Queue()
        self.accepting = False
        self.started = False
        self.events = events
        self.fail_start = False
        self.start_error_code = "microphone_open_failed"
        self.read_error: VoiceFatalError | None = None
        self.discard_count = 0

    async def start(self) -> None:
        if self.events is not None:
            self.events.append("input:start")
        if self.fail_start:
            raise VoiceFatalError(self.start_error_code)
        self.started = True
        self.accepting = True

    async def stop(self) -> None:
        if self.events is not None:
            self.events.append("input:stop")
        self.started = False

    async def read(self, timeout_seconds: float | None = None) -> AudioChunk:
        if self.read_error is not None:
            error, self.read_error = self.read_error, None
            raise error
        if timeout_seconds is None:
            return await self.queue.get()
        async with asyncio.timeout(timeout_seconds):
            return await self.queue.get()

    def set_accepting(self, enabled: bool) -> None:
        self.accepting = enabled

    def discard_pending(self) -> None:
        self.discard_count += 1
        while True:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def feed(self, pcm: bytes, captured_at: float) -> None:
        if self.accepting:
            self.queue.put_nowait(AudioChunk(pcm=pcm, captured_at=captured_at))


class FakeWakeWord:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events
        self.detect_count = 0
        self.reset_count = 0

    async def start(self) -> None:
        if self.events is not None:
            self.events.append("wake:start")

    async def stop(self) -> None:
        if self.events is not None:
            self.events.append("wake:stop")

    async def detect(self, _pcm: bytes) -> bool:
        self.detect_count += 1
        return self.detect_count == 1

    def reset(self) -> None:
        self.reset_count += 1


class FakeRecorder:
    def __init__(self, results: list[tuple[object | None, RecordingEnd]]) -> None:
        self.results = deque(results)
        self.calls: list[dict[str, object]] = []
        self.gate: asyncio.Event | None = None

    async def record(self, _audio: object, **kwargs: object):
        self.calls.append(kwargs)
        if self.gate is not None:
            await self.gate.wait()
        return self.results.popleft()


class FakeGateway:
    def __init__(self, transcripts: list[str]) -> None:
        self.transcripts = deque(transcripts)
        self.transcribe_error: OpenAiTurnError | None = None
        self.closed = 0
        self.synthesized: list[str] = []
        self.transcribe_gate: asyncio.Event | None = None

    async def transcribe(self, _utterance: object) -> str:
        if self.transcribe_gate is not None:
            await self.transcribe_gate.wait()
        if self.transcribe_error is not None:
            raise self.transcribe_error
        return self.transcripts.popleft()

    def synthesize(self, text: str):
        self.synthesized.append(text)

        async def chunks():
            yield b"\x01\x02"

        return chunks()

    async def close(self) -> None:
        self.closed += 1


class FakeAssistant:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def reply(self, text: str) -> AssistantReply:
        self.texts.append(text)
        return AssistantReply(spoken_text=f"응답 {len(self.texts)}")


class FakePlayback:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events
        self.effects: list[EffectName] = []
        self.speeches = 0
        self.fail_effect: VoiceFatalError | None = None
        self.speech_gate: asyncio.Event | None = None

    async def start(self) -> None:
        if self.events is not None:
            self.events.append("playback:start")

    async def stop(self) -> None:
        if self.events is not None:
            self.events.append("playback:stop")

    async def stop_speech(self) -> None:
        return None

    async def play_effect(self, effect: EffectName) -> None:
        if self.fail_effect is not None:
            raise self.fail_effect
        self.effects.append(effect)

    async def play_speech(self, chunks: object) -> None:
        self.speeches += 1
        if self.speech_gate is not None:
            await self.speech_gate.wait()
        async for _chunk in chunks:  # type: ignore[union-attr]
            pass


async def wait_for_state(service: VoiceService, state: VoiceState) -> None:
    async with asyncio.timeout(1):
        while service.get_snapshot().state is not state:
            await asyncio.sleep(0)


def make_service(
    *,
    recorder_results: list[tuple[object | None, RecordingEnd]],
    transcripts: list[str],
    events: list[str] | None = None,
) -> tuple[
    VoiceService,
    FakeAudioInput,
    FakeWakeWord,
    FakeGateway,
    FakeAssistant,
    FakePlayback,
]:
    audio = FakeAudioInput(events)
    wakeword = FakeWakeWord(events)
    gateway = FakeGateway(transcripts)
    assistant = FakeAssistant()
    playback = FakePlayback(events)
    service = VoiceService(
        audio_input=audio,
        wakeword=wakeword,
        recorder=FakeRecorder(recorder_results),  # type: ignore[arg-type]
        gateway=gateway,  # type: ignore[arg-type]
        assistant=assistant,  # type: ignore[arg-type]
        playback=playback,  # type: ignore[arg-type]
        settings=VoiceSettings(
            speech_start_timeout_seconds=0.1,
            followup_timeout_seconds=0.2,
            post_playback_guard_seconds=0.01,
        ),
        task_manager=TaskManager(),
    )
    return service, audio, wakeword, gateway, assistant, playback


async def test_wake_turn_and_followup_repeat_without_second_wake_word() -> None:
    utterance = build_wav([pcm_frame(700)] * 4)
    service, audio, wakeword, gateway, assistant, playback = make_service(
        recorder_results=[
            (utterance, RecordingEnd.SILENCE),
            (utterance, RecordingEnd.SILENCE),
        ],
        transcripts=["첫 질문", "후속 질문"],
    )
    await service.start()
    await wait_for_state(service, VoiceState.WAITING_WAKE)

    audio.feed(pcm_frame(0), 1.0)
    await wait_for_state(service, VoiceState.WAITING_FOLLOWUP)
    first_expiry = service.get_snapshot().followup_expires_at
    audio.feed(pcm_frame(900), 2.0)
    await asyncio.sleep(0.03)
    await wait_for_state(service, VoiceState.WAITING_FOLLOWUP)

    assert assistant.texts == ["첫 질문", "후속 질문"]
    assert gateway.synthesized == ["응답 1", "응답 2"]
    assert wakeword.detect_count == 1
    assert playback.effects == [EffectName.ACKNOWLEDGEMENT]
    assert service.get_snapshot().followup_expires_at != first_expiry
    await service.stop()
    assert service.get_snapshot().state is VoiceState.DISABLED


async def test_too_short_followup_keeps_original_deadline() -> None:
    utterance = build_wav([pcm_frame(700)] * 4)
    service, audio, _wakeword, _gateway, _assistant, _playback = make_service(
        recorder_results=[
            (utterance, RecordingEnd.SILENCE),
            (None, RecordingEnd.TOO_SHORT),
        ],
        transcripts=["첫 질문"],
    )
    await service.start()
    audio.feed(pcm_frame(0), 1.0)
    await wait_for_state(service, VoiceState.WAITING_FOLLOWUP)
    original_expiry = service.get_snapshot().followup_expires_at

    audio.feed(pcm_frame(900), 2.0)
    await asyncio.sleep(0)
    await wait_for_state(service, VoiceState.WAITING_FOLLOWUP)

    assert service.get_snapshot().followup_expires_at == original_expiry
    await service.stop()


async def test_recoverable_stt_error_returns_to_wake_waiting() -> None:
    utterance = build_wav([pcm_frame(700)] * 4)
    service, audio, _wakeword, gateway, _assistant, playback = make_service(
        recorder_results=[(utterance, RecordingEnd.SILENCE)],
        transcripts=[],
    )
    gateway.transcribe_error = OpenAiTurnError(stage="stt", code="stt_timeout")
    await service.start()
    audio.feed(pcm_frame(0), 1.0)

    async with asyncio.timeout(1):
        while service.get_snapshot().last_error != "stt_timeout":
            await asyncio.sleep(0)
    assert service.get_snapshot().state is VoiceState.WAITING_WAKE
    assert service.get_snapshot().last_error == "stt_timeout"
    assert playback.effects == [EffectName.ACKNOWLEDGEMENT, EffectName.ERROR]
    await service.stop()


async def test_fatal_speaker_error_moves_only_voice_to_error() -> None:
    utterance = build_wav([pcm_frame(700)] * 4)
    service, audio, _wakeword, _gateway, _assistant, playback = make_service(
        recorder_results=[(utterance, RecordingEnd.SILENCE)],
        transcripts=["질문"],
    )
    playback.fail_effect = VoiceFatalError("speaker_failed")
    await service.start()
    audio.feed(pcm_frame(0), 1.0)

    await wait_for_state(service, VoiceState.ERROR)
    assert service.get_snapshot().last_error == "speaker_failed"
    await service.stop()


async def test_device_start_failure_retries_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    service, audio, _wakeword, gateway, _assistant, _playback = make_service(
        recorder_results=[],
        transcripts=[],
        events=events,
    )
    audio.fail_start = True
    monkeypatch.setattr(
        "smart_desk.modules.voice.service.DEVICE_RETRY_INTERVAL_SECONDS",
        0.01,
    )

    await service.start()
    await wait_for_state(service, VoiceState.ERROR)
    assert gateway.closed == 0

    audio.fail_start = False
    await wait_for_state(service, VoiceState.WAITING_WAKE)

    assert events[:5] == [
        "wake:start",
        "playback:start",
        "input:start",
        "playback:stop",
        "wake:stop",
    ]
    assert events[5:] == ["wake:start", "playback:start", "input:start"]
    assert service.get_snapshot().last_error is None
    await service.stop()
    assert gateway.closed == 1


async def test_runtime_microphone_disconnect_retries_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, audio, _wakeword, gateway, _assistant, _playback = make_service(
        recorder_results=[],
        transcripts=[],
    )
    monkeypatch.setattr(
        "smart_desk.modules.voice.service.DEVICE_RETRY_INTERVAL_SECONDS",
        0.01,
    )
    await service.start()
    await wait_for_state(service, VoiceState.WAITING_WAKE)

    audio.fail_start = True
    audio.read_error = VoiceFatalError("microphone_inactive")
    await wait_for_state(service, VoiceState.ERROR)
    assert service.get_snapshot().last_error == "microphone_inactive"
    assert gateway.closed == 0

    audio.fail_start = False
    await wait_for_state(service, VoiceState.WAITING_WAKE)
    await service.stop()
    assert gateway.closed == 1


async def test_nonrecoverable_start_failure_stays_in_error() -> None:
    service, audio, _wakeword, gateway, _assistant, _playback = make_service(
        recorder_results=[],
        transcripts=[],
    )
    audio.fail_start = True
    audio.start_error_code = "wakeword_model_invalid"

    await service.start()
    await wait_for_state(service, VoiceState.ERROR)
    await asyncio.sleep(0.01)

    assert service.get_snapshot().last_error == "wakeword_model_invalid"
    assert gateway.closed == 0
    await service.stop()
    assert gateway.closed == 1


async def test_stop_is_safe_while_waiting_for_wake() -> None:
    service, _audio, _wakeword, gateway, _assistant, _playback = make_service(
        recorder_results=[],
        transcripts=[],
    )
    await service.start()
    await wait_for_state(service, VoiceState.WAITING_WAKE)

    await service.stop()
    await service.stop()

    assert service.get_snapshot().state is VoiceState.DISABLED
    assert gateway.closed == 1


@pytest.mark.parametrize("blocked_state", [VoiceState.PROCESSING, VoiceState.SPEAKING])
async def test_stop_cancels_processing_and_speaking(
    blocked_state: VoiceState,
) -> None:
    utterance = build_wav([pcm_frame(700)] * 4)
    service, audio, _wakeword, gateway, _assistant, playback = make_service(
        recorder_results=[(utterance, RecordingEnd.SILENCE)],
        transcripts=["질문"],
    )
    if blocked_state is VoiceState.PROCESSING:
        gateway.transcribe_gate = asyncio.Event()
    else:
        playback.speech_gate = asyncio.Event()
    await service.start()
    audio.feed(pcm_frame(0), 1.0)
    await wait_for_state(service, blocked_state)

    await service.stop()

    assert service.get_snapshot().state is VoiceState.DISABLED
    assert gateway.closed == 1
