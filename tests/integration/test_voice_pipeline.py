"""장치와 network 없이 실제 Voice 구성요소를 연결하는 통합 테스트."""

import asyncio
from collections import deque
from pathlib import Path
import struct
import time

from smart_desk.config.settings import VoiceSettings
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.assistant.models import AssistantReply, OpenAiTurn
from smart_desk.modules.assistant.service import AssistantService
from smart_desk.modules.voice.audio import RmsRecorder
from smart_desk.modules.voice.models import (
    AudioChunk,
    EffectName,
    INPUT_FRAME_SAMPLES,
    VoiceState,
)
from smart_desk.modules.voice.playback import PlaybackCoordinator
from smart_desk.modules.voice.service import VoiceService


def pcm_frame(value: int) -> bytes:
    return struct.pack("<h", value) * INPUT_FRAME_SAMPLES


class FakeAudioInput:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[AudioChunk] = asyncio.Queue(maxsize=16)
        self.accepting = False
        self.rejected_frames = 0

    async def start(self) -> None:
        self.accepting = True

    async def stop(self) -> None:
        self.accepting = False
        self.discard_pending()

    async def read(self, timeout_seconds: float | None = None) -> AudioChunk:
        if timeout_seconds is None:
            return await self.queue.get()
        async with asyncio.timeout(timeout_seconds):
            return await self.queue.get()

    def set_accepting(self, enabled: bool) -> None:
        self.accepting = enabled

    def discard_pending(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def feed(self, chunks: list[AudioChunk]) -> None:
        for chunk in chunks:
            if self.accepting:
                self.queue.put_nowait(chunk)
            else:
                self.rejected_frames += 1


class FakeWakeWordDetector:
    def __init__(self) -> None:
        self.detect_count = 0

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def detect(self, _pcm: bytes) -> bool:
        self.detect_count += 1
        return True

    def reset(self) -> None:
        return None


class FakeOpenAiGateway:
    def __init__(self) -> None:
        self.transcripts = deque(["첫 질문", "둘째 질문", "셋째 질문"])
        self.response_requests: list[dict[str, object]] = []
        self.speech_started = asyncio.Event()
        self.release_speech = asyncio.Event()
        self.block_speech_number: int | None = 2
        self.speech_count = 0
        self.closed = False

    async def transcribe(self, _utterance: object) -> str:
        return self.transcripts.popleft()

    async def create_response(self, **request: object) -> OpenAiTurn:
        self.response_requests.append(request)
        number = len(self.response_requests)
        return OpenAiTurn(
            reply=AssistantReply(spoken_text=f"통합 응답 {number}", next_action="WAIT_FOR_FOLLOWUP", decision_reason="ASSISTANT_REQUESTED_INPUT"),
            output_items=(
                {"type": "reasoning", "encrypted_content": f"encrypted-{number}"},
                {"type": "message", "id": f"message-{number}"},
            ),
            request_id=f"req-{number}",
            input_tokens=number,
            output_tokens=number,
        )

    def synthesize(self, _text: str):
        self.speech_count += 1
        number = self.speech_count

        async def stream():
            yield b"\x01\x02"
            if number == self.block_speech_number:
                self.speech_started.set()
                await self.release_speech.wait()
            yield b"\x03\x04"

        return stream()

    async def close(self) -> None:
        self.closed = True


class FakePcmOutput:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def write(self, pcm: bytes) -> None:
        self.writes.append(pcm)

    async def drain(self) -> None:
        return None

    async def abort(self) -> None:
        return None

    async def stop(self) -> None:
        self.started = False


async def wait_for_state(service: VoiceService, state: VoiceState) -> None:
    async with asyncio.timeout(1):
        while service.get_snapshot().state is not state:
            await asyncio.sleep(0)


async def feed_utterance(audio: FakeAudioInput, *, base: float) -> None:
    async with asyncio.timeout(1):
        while not audio.accepting:
            await asyncio.sleep(0)
    values = [900, 900, 0, 0, 0]
    audio.feed(
        [
            AudioChunk(pcm=pcm_frame(value), captured_at=base + index * 0.08)
            for index, value in enumerate(values)
        ]
    )


async def test_three_turn_pipeline_reuses_history_and_discards_tts_input() -> None:
    audio = FakeAudioInput()
    wakeword = FakeWakeWordDetector()
    gateway = FakeOpenAiGateway()
    output = FakePcmOutput()
    playback = PlaybackCoordinator(
        output,
        acknowledgement_effect_path=Path(
            "assets/voice/effects/acknowledgement.wav"
        ),
        error_effect_path=Path("assets/voice/effects/error.wav"),
    )
    assistant = AssistantService(gateway, session_max_turns=12)
    settings = VoiceSettings(
        silence_duration_seconds=0.24,
        min_utterance_seconds=0.16,
        speech_start_timeout_seconds=0.5,
        followup_timeout_seconds=1.0,
        followup_preroll_seconds=0.08,
        post_playback_guard_seconds=0,
        input_queue_frames=16,
    )
    service = VoiceService(
        audio_input=audio,
        wakeword=wakeword,
        recorder=RmsRecorder(
            rms_threshold=settings.silence_rms_threshold,
            speech_start_consecutive_frames=settings.speech_start_consecutive_frames,
            silence_duration_seconds=settings.silence_duration_seconds,
            min_utterance_seconds=settings.min_utterance_seconds,
            max_utterance_seconds=settings.max_utterance_seconds,
            preroll_seconds=settings.followup_preroll_seconds,
        ),
        gateway=gateway,
        assistant=assistant,
        playback=playback,
        settings=settings,
        task_manager=TaskManager(),
    )

    await service.start()
    await wait_for_state(service, VoiceState.WAITING_WAKE)
    audio.feed([AudioChunk(pcm=pcm_frame(0), captured_at=time.monotonic())])
    await wait_for_state(service, VoiceState.RECORDING)
    await feed_utterance(audio, base=time.monotonic())
    await wait_for_state(service, VoiceState.WAITING_FOLLOWUP)

    await feed_utterance(audio, base=time.monotonic())
    await gateway.speech_started.wait()
    assert service.get_snapshot().state is VoiceState.SPEAKING
    audio.feed([AudioChunk(pcm=pcm_frame(900), captured_at=time.monotonic())])
    gateway.release_speech.set()
    await wait_for_state(service, VoiceState.WAITING_FOLLOWUP)
    assert audio.queue.empty()

    await feed_utterance(audio, base=time.monotonic())
    async with asyncio.timeout(1):
        while len(gateway.response_requests) < 3:
            await asyncio.sleep(0)
    await wait_for_state(service, VoiceState.WAITING_FOLLOWUP)

    assert wakeword.detect_count == 1
    assert len(gateway.response_requests) == 3
    assert gateway.response_requests[0]["history"] == ()
    assert gateway.response_requests[1]["history"] == (
        {"role": "user", "content": "첫 질문"},
        {"type": "reasoning", "encrypted_content": "encrypted-1"},
        {"type": "message", "id": "message-1"},
    )
    assert len(gateway.response_requests[2]["history"]) == 6  # type: ignore[arg-type]
    assert audio.rejected_frames == 1
    assert output.writes[0] != b"\x01\x02"  # acknowledgement effect가 먼저 재생됨

    await service.stop()
    assert service.get_snapshot().state is VoiceState.DISABLED
    assert gateway.closed is True
    assert output.started is False
