"""OpenAI Agents SDK 음성 실행의 작은 프로젝트 경계.

이 모듈을 import하는 것만으로 optional ``agents`` dependency를 요구하지 않는다.
실제 SDK 조립은 :meth:`AgentsVoiceRuntime.build`에서만 수행한다.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

import numpy as np


class VoiceRuntimeEventType(StrEnum):
    """Voice 하드웨어 계층이 처리할 provider-neutral event 종류."""

    LIFECYCLE = "lifecycle"
    TRANSCRIPT = "transcript"
    AUDIO = "audio"
    ERROR = "error"


class VoiceRuntimeLifecycle(StrEnum):
    TURN_STARTED = "turn_started"
    TURN_ENDED = "turn_ended"
    SESSION_ENDED = "session_ended"


@dataclass(frozen=True, slots=True)
class AgentsVoiceConfig:
    """현재 단일 Smart Desk voice 경로의 고정 조립값."""

    model: str = "gpt-5.6-terra"
    reasoning_effort: str = "low"
    stt_model: str = "gpt-4o-transcribe"
    tts_model: str = "tts-1"
    vad_threshold: float = 0.5
    vad_prefix_padding_ms: int = 300
    vad_silence_duration_ms: int = 600


@dataclass(frozen=True, slots=True)
class VoiceRuntimeEvent:
    """하나의 runtime 실행에서 순서가 보장되는 안전한 공개 event다."""

    sequence: int
    type: VoiceRuntimeEventType
    lifecycle: VoiceRuntimeLifecycle | None = None
    audio: bytes | None = None
    transcript: str | None = None
    error_code: str | None = None


FinalTranscriptCallback = Callable[[str], Awaitable[None] | None]


class VoiceResult(Protocol):
    def stream(self) -> AsyncIterator[object]: ...


class VoicePipelinePort(Protocol):
    async def run(self, audio_input: object) -> VoiceResult: ...


class StreamedAudioInputPort(Protocol):
    async def add_audio(self, audio: np.ndarray | None) -> None: ...


class VoiceTurnError(Exception):
    """SDK 원문이나 transcript를 노출하지 않는 runtime 내부 오류."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(slots=True)
class SmartDeskVoiceWorkflow:
    """단일 Agent의 text streaming workflow.

    Session/memory/tool/follow-up 연결은 08B의 extension point로 남긴다. STT가 확정한
    transcription만 이 workflow에 들어오므로 partial transcript 부작용은 없다.
    """

    agent: Any
    run_streamed: Callable[..., Any]
    stream_text_from: Callable[[Any], AsyncIterator[str]]
    on_final_transcript: FinalTranscriptCallback | None = None
    _input_history: list[Any] = field(default_factory=list)
    _runtime_final_transcript_sink: FinalTranscriptCallback | None = field(
        default=None, init=False, repr=False
    )

    def _set_runtime_final_transcript_sink(
        self, sink: FinalTranscriptCallback | None
    ) -> None:
        """현재 half-duplex run의 짧은 transcript side channel만 바꾼다."""
        self._runtime_final_transcript_sink = sink

    async def run(self, transcription: str) -> AsyncIterator[str]:
        if self.on_final_transcript is not None:
            callback_result = self.on_final_transcript(transcription)
            if inspect.isawaitable(callback_result):
                await callback_result
        if self._runtime_final_transcript_sink is not None:
            sink_result = self._runtime_final_transcript_sink(transcription)
            if inspect.isawaitable(sink_result):
                await sink_result

        self._input_history.append({"role": "user", "content": transcription})
        result = self.run_streamed(self.agent, self._input_history)
        async for text in self.stream_text_from(result):
            yield text
        self._input_history = result.to_input_list()
        self.agent = result.last_agent


class AgentsVoiceRuntime:
    """24kHz streamed PCM을 VoicePipeline에 직접 연결한다."""

    def __init__(
        self,
        pipeline: VoicePipelinePort,
        streamed_input_factory: Callable[[], StreamedAudioInputPort],
        *,
        workflow: SmartDeskVoiceWorkflow | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._streamed_input_factory = streamed_input_factory
        self._workflow = workflow
        self._run_in_progress = False

    @classmethod
    def build(
        cls,
        *,
        config: AgentsVoiceConfig = AgentsVoiceConfig(),
        on_final_transcript: FinalTranscriptCallback | None = None,
    ) -> AgentsVoiceRuntime:
        # optional dependency imports: Voice disabled 상태의 app import를 막지 않는다.
        from agents import Agent, ModelSettings, Runner
        from agents.voice import StreamedAudioInput, VoicePipeline, VoiceWorkflowHelper

        agent = Agent(
            name="Smart Desk",
            model=config.model,
            model_settings=ModelSettings(reasoning={"effort": config.reasoning_effort}),
            instructions=(
                "You are a concise Korean Smart Desk voice assistant. "
                "Do not claim physical actions without a provided tool."
            ),
        )
        workflow = SmartDeskVoiceWorkflow(
            agent=agent,
            run_streamed=Runner.run_streamed,
            stream_text_from=VoiceWorkflowHelper.stream_text_from,
            on_final_transcript=on_final_transcript,
        )
        pipeline = VoicePipeline(
            workflow=workflow,
            stt_model=config.stt_model,
            tts_model=config.tts_model,
            config={
                "tracing_disabled": True,
                "trace_include_sensitive_data": False,
                "trace_include_sensitive_audio_data": False,
                "stt_settings": {
                    "language": "ko",
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": config.vad_threshold,
                        "prefix_padding_ms": config.vad_prefix_padding_ms,
                        "silence_duration_ms": config.vad_silence_duration_ms,
                    },
                },
            },
        )
        return cls(pipeline, StreamedAudioInput, workflow=workflow)

    async def run_audio(self, chunks: AsyncIterable[bytes]) -> AsyncIterator[VoiceRuntimeEvent]:
        """24kHz mono PCM16 chunk stream 하나를 SDK stream 하나에 연결한다.

        input 종료에서는 반드시 ``None``을 전달한다. 소비자 취소·오류에서는 feeder와
        SDK result stream을 함께 닫아 그 뒤의 event를 외부로 내보내지 않는다.
        """
        audio_input = self._streamed_input_factory()
        if self._run_in_progress:
            yield VoiceRuntimeEvent(
                sequence=1,
                type=VoiceRuntimeEventType.ERROR,
                error_code="voice_pipeline_failed",
            )
            return

        self._run_in_progress = True
        feeder = asyncio.create_task(self._feed_audio(audio_input, chunks))
        result_stream: AsyncIterator[object] | None = None
        pipeline_wait: asyncio.Task[VoiceResult] | None = None
        result_wait: asyncio.Task[object] | None = None
        transcript_wait: asyncio.Task[str] | None = None
        transcript_channel: asyncio.Queue[str] | None = None
        sequence = 0
        saw_sdk_event = False

        if self._workflow is not None:
            transcript_channel = asyncio.Queue()

            async def publish_final_transcript(transcript: str) -> None:
                # 이 runtime은 VoiceService의 half-duplex turn 하나에만 쓰인다. 범용
                # multi-run broker 없이 현재 run의 작은 queue에만 전달한다.
                transcript_channel.put_nowait(transcript)

            self._workflow._set_runtime_final_transcript_sink(publish_final_transcript)
        try:
            pipeline_wait = asyncio.create_task(self._pipeline.run(audio_input))
            # pipeline이 input/STT를 기다리기 전에 feeder가 실패한 경우에도 둘 중 하나를
            # 기다려 fail-closed한다. 그렇지 않으면 invalid PCM turn이 SDK 안에 남을 수 있다.
            pipeline_or_feeder, _ = await asyncio.wait(
                {pipeline_wait, feeder}, return_when=asyncio.FIRST_COMPLETED
            )
            if feeder in pipeline_or_feeder:
                feeder.result()
            result = await pipeline_wait
            result_stream = result.stream()
            result_wait = asyncio.create_task(anext(result_stream))
            if transcript_channel is not None:
                transcript_wait = asyncio.create_task(transcript_channel.get())

            while result_wait is not None:
                if feeder.done():
                    feeder.result()
                wait_for: set[asyncio.Task[Any]] = {result_wait}
                if not feeder.done():
                    wait_for.add(feeder)
                # turn_started가 stream의 첫 event라면 transcript보다 먼저 내보낸다.
                # 첫 SDK event를 보기 전에는 side channel만으로 진행하지 않는다.
                if transcript_wait is not None and (saw_sdk_event or result_wait.done()):
                    wait_for.add(transcript_wait)
                done, _ = await asyncio.wait(wait_for, return_when=asyncio.FIRST_COMPLETED)

                if feeder in done:
                    feeder.result()  # feeder 오류는 fail-closed로 즉시 종료한다.

                if result_wait in done:
                    try:
                        sdk_event = result_wait.result()
                    except StopAsyncIteration:
                        result_wait = None
                    else:
                        saw_sdk_event = True
                        result_wait = asyncio.create_task(anext(result_stream))
                        mapped = self._map_event(sdk_event, sequence + 1)
                        if mapped is not None:
                            # workflow callback은 TTS audio보다 먼저 queue에 넣는다. audio를
                            # 내보내기 직전에 완료된 transcript를 먼저 drain해 그 순서를 고정한다.
                            if mapped.type is VoiceRuntimeEventType.AUDIO:
                                while transcript_wait is not None and transcript_wait.done():
                                    transcript = transcript_wait.result()
                                    sequence += 1
                                    yield VoiceRuntimeEvent(
                                        sequence=sequence,
                                        type=VoiceRuntimeEventType.TRANSCRIPT,
                                        transcript=transcript,
                                    )
                                    transcript_wait = asyncio.create_task(transcript_channel.get())
                            sequence = mapped.sequence if mapped.sequence > sequence else sequence + 1
                            yield VoiceRuntimeEvent(
                                sequence=sequence,
                                type=mapped.type,
                                lifecycle=mapped.lifecycle,
                                audio=mapped.audio,
                                transcript=mapped.transcript,
                                error_code=mapped.error_code,
                            )
                            if mapped.type is VoiceRuntimeEventType.ERROR:
                                return

                if transcript_wait is not None and transcript_wait in done:
                    transcript = transcript_wait.result()
                    sequence += 1
                    yield VoiceRuntimeEvent(
                        sequence=sequence,
                        type=VoiceRuntimeEventType.TRANSCRIPT,
                        transcript=transcript,
                    )
                    transcript_wait = asyncio.create_task(transcript_channel.get())

            await feeder
            # result 종료와 callback 완료가 같은 event-loop tick에서 맞물릴 수 있다.
            # 한 번 양보한 뒤 이미 전달된 final transcript만 drain하고 늦은 것은 버린다.
            await asyncio.sleep(0)
            while transcript_wait is not None and transcript_wait.done():
                transcript = transcript_wait.result()
                sequence += 1
                yield VoiceRuntimeEvent(
                    sequence=sequence,
                    type=VoiceRuntimeEventType.TRANSCRIPT,
                    transcript=transcript,
                )
                transcript_wait = asyncio.create_task(transcript_channel.get())
        except asyncio.CancelledError:
            raise
        except Exception:
            # SDK exception/original transcript/provider error는 공개 event에 포함하지 않는다.
            sequence += 1
            yield VoiceRuntimeEvent(
                sequence=sequence,
                type=VoiceRuntimeEventType.ERROR,
                error_code="voice_pipeline_failed",
            )
        finally:
            self._run_in_progress = False
            if self._workflow is not None:
                self._workflow._set_runtime_final_transcript_sink(None)
            if pipeline_wait is not None and not pipeline_wait.done():
                pipeline_wait.cancel()
            if not feeder.done():
                feeder.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await feeder
            for task in (pipeline_wait, result_wait, transcript_wait):
                if task is not None and not task.done():
                    task.cancel()
            for task in (pipeline_wait, result_wait, transcript_wait):
                if task is not None:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
            # task가 한번도 scheduling 되기 전에 consumer가 닫은 경우에는 feeder의 finally가
            # 실행되지 않는다. 이 경우에도 STT queue를 확실히 깨운다.
            if feeder.cancelled():
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await audio_input.add_audio(None)
            if result_stream is not None:
                aclose = getattr(result_stream, "aclose", None)
                if aclose is not None:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await aclose()

    async def _feed_audio(
        self, audio_input: StreamedAudioInputPort, chunks: AsyncIterable[bytes]
    ) -> None:
        try:
            async for chunk in chunks:
                if len(chunk) % 2:
                    raise VoiceTurnError("invalid_pcm16")
                # frombuffer view only: Wake Word resampling과 별개로 원본 bytes는 바꾸지 않는다.
                await audio_input.add_audio(np.frombuffer(chunk, dtype="<i2"))
        finally:
            # 정상 종료와 producer 오류 모두 STT session이 대기하지 않도록 sentinel을 보낸다.
            await audio_input.add_audio(None)

    @staticmethod
    def _map_event(event: object, sequence: int) -> VoiceRuntimeEvent | None:
        event_type = getattr(event, "type", "")
        if event_type == "voice_stream_event_audio":
            data = getattr(event, "data", None)
            if data is not None:
                return VoiceRuntimeEvent(
                    sequence=sequence,
                    type=VoiceRuntimeEventType.AUDIO,
                    audio=np.asarray(data, dtype=np.int16).tobytes(),
                )
        elif event_type == "voice_stream_event_lifecycle":
            lifecycle = getattr(event, "event", "")
            try:
                return VoiceRuntimeEvent(
                    sequence=sequence,
                    type=VoiceRuntimeEventType.LIFECYCLE,
                    lifecycle=VoiceRuntimeLifecycle(lifecycle),
                )
            except ValueError:
                return None
        elif event_type == "voice_stream_event_error":
            return VoiceRuntimeEvent(
                sequence=sequence,
                type=VoiceRuntimeEventType.ERROR,
                error_code="voice_pipeline_failed",
            )
        return None
