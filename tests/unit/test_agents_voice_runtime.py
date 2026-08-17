"""AgentsVoiceRuntime의 streaming/cancel/event 계약을 fake로 고정한다."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator

import numpy as np

from smart_desk.modules.assistant.agents_runtime import (
    AgentsVoiceRuntime,
    SmartDeskVoiceWorkflow,
    VoiceRuntimeEventType,
)


class Event:
    def __init__(self, type: str, **values: object) -> None:
        self.type = type
        for key, value in values.items():
            setattr(self, key, value)


class Input:
    def __init__(self) -> None:
        self.items: list[np.ndarray | None] = []

    async def add_audio(self, audio: np.ndarray | None) -> None:
        self.items.append(audio)


class Result:
    def __init__(self, events: list[object], *, wait: bool = False) -> None:
        self.events = events
        self.wait = wait
        self.closed = False

    async def stream(self) -> AsyncIterator[object]:
        try:
            for event in self.events:
                yield event
                if self.wait:
                    await asyncio.sleep(10)
        finally:
            self.closed = True


class Pipeline:
    def __init__(self, result: Result) -> None:
        self.result = result
        self.inputs: list[Input] = []

    async def run(self, audio_input: Input) -> Result:
        self.inputs.append(audio_input)
        return self.result


class WorkflowPipeline:
    """실제 SDK 없이 workflow callback과 SDK audio stream의 경합을 재현한다."""

    def __init__(
        self, workflow: SmartDeskVoiceWorkflow, transcript: str, *, wait: bool = False
    ) -> None:
        self.workflow = workflow
        self.transcript = transcript
        self.wait = wait
        self.result = Result([])

    async def run(self, _: Input) -> Result:
        async def stream() -> AsyncIterator[object]:
            try:
                async for _ in self.workflow.run(self.transcript):
                    pass
                yield Event("voice_stream_event_lifecycle", event="turn_started")
                if self.wait:
                    await asyncio.sleep(10)
                yield Event("voice_stream_event_audio", data=np.array([7], dtype=np.int16))
            finally:
                self.result.closed = True

        self.result.stream = stream  # type: ignore[method-assign]
        return self.result


async def chunks(*items: bytes) -> AsyncIterable[bytes]:
    for item in items:
        yield item


async def test_runtime_streams_24khz_chunks_sentinel_and_sequenced_events() -> None:
    input_value = Input()
    pipeline = Pipeline(Result([
        Event("voice_stream_event_lifecycle", event="turn_started"),
        Event("voice_stream_event_audio", data=np.array([1, -2], dtype=np.int16)),
        Event("voice_stream_event_lifecycle", event="turn_ended"),
    ]))
    runtime = AgentsVoiceRuntime(pipeline, lambda: input_value)

    events = [event async for event in runtime.run_audio(chunks(b"\x01\x00", b"\xfe\xff"))]

    assert [event.type for event in events] == [
        VoiceRuntimeEventType.LIFECYCLE,
        VoiceRuntimeEventType.AUDIO,
        VoiceRuntimeEventType.LIFECYCLE,
    ]
    assert [event.sequence for event in events] == [1, 2, 3]
    assert events[1].audio == b"\x01\x00\xfe\xff"
    assert [item.tolist() if item is not None else None for item in input_value.items] == [
        [1], [-2], None,
    ]
    assert all(item is None or item.dtype == np.dtype("int16") for item in input_value.items)


async def test_runtime_hides_provider_error_and_stops_late_events() -> None:
    runtime = AgentsVoiceRuntime(
        Pipeline(Result([
            Event("voice_stream_event_error", error=RuntimeError("secret transcript")),
            Event("voice_stream_event_audio", data=np.array([99], dtype=np.int16)),
        ])),
        Input,
    )

    events = [event async for event in runtime.run_audio(chunks(b"\0\0"))]

    assert [(event.type, event.error_code) for event in events] == [
        (VoiceRuntimeEventType.ERROR, "voice_pipeline_failed"),
    ]


async def test_runtime_consumer_cancel_closes_feeder_and_sdk_stream() -> None:
    input_value = Input()
    result = Result([Event("voice_stream_event_lifecycle", event="turn_started")], wait=True)
    stream = AgentsVoiceRuntime(Pipeline(result), lambda: input_value).run_audio(chunks(b"\0\0"))

    await anext(stream)
    await stream.aclose()

    assert result.closed is True
    assert input_value.items[-1] is None


async def test_runtime_rejects_odd_pcm_without_leaking_error_text() -> None:
    runtime = AgentsVoiceRuntime(Pipeline(Result([])), Input)

    events = [event async for event in runtime.run_audio(chunks(b"\0"))]

    assert [(event.type, event.error_code) for event in events] == [
        (VoiceRuntimeEventType.ERROR, "voice_pipeline_failed"),
    ]


async def test_workflow_sends_only_final_transcript_to_callback_and_streams_runner_text() -> None:
    received: list[str] = []

    class RunResult:
        last_agent = "next-agent"

        def to_input_list(self) -> list[object]:
            return ["history"]

    def run_streamed(agent: object, history: list[object]) -> RunResult:
        assert agent == "agent"
        assert history == [{"role": "user", "content": "책상을 올려줘"}]
        return RunResult()

    async def stream_text(_: RunResult) -> AsyncIterator[str]:
        yield "네, "
        yield "확인하겠습니다."

    workflow = SmartDeskVoiceWorkflow(
        agent="agent",
        run_streamed=run_streamed,
        stream_text_from=stream_text,
        on_final_transcript=received.append,
    )

    assert [text async for text in workflow.run("책상을 올려줘")] == ["네, ", "확인하겠습니다."]
    assert received == ["책상을 올려줘"]
    assert workflow._input_history == ["history"]


async def test_runtime_merges_final_transcript_before_matching_audio_with_sequence() -> None:
    received: list[str] = []

    class RunResult:
        last_agent = "agent"

        def to_input_list(self) -> list[object]:
            return []

    async def no_text(_: RunResult) -> AsyncIterator[str]:
        if False:
            yield ""

    workflow = SmartDeskVoiceWorkflow(
        agent="agent",
        run_streamed=lambda *_: RunResult(),
        stream_text_from=no_text,
        on_final_transcript=received.append,
    )
    runtime = AgentsVoiceRuntime(
        WorkflowPipeline(workflow, "확정된 문장"), Input, workflow=workflow
    )

    events = [event async for event in runtime.run_audio(chunks(b"\0\0"))]

    assert [event.type for event in events] == [
        VoiceRuntimeEventType.LIFECYCLE,
        VoiceRuntimeEventType.TRANSCRIPT,
        VoiceRuntimeEventType.AUDIO,
    ]
    assert [event.sequence for event in events] == [1, 2, 3]
    assert events[1].transcript == "확정된 문장"
    assert received == ["확정된 문장"]


async def test_runtime_callback_failure_is_fail_closed_without_transcript() -> None:
    class RunResult:
        last_agent = "agent"

        def to_input_list(self) -> list[object]:
            return []

    async def no_text(_: RunResult) -> AsyncIterator[str]:
        if False:
            yield ""

    def callback(_: str) -> None:
        raise RuntimeError("do not expose transcript")

    workflow = SmartDeskVoiceWorkflow(
        agent="agent",
        run_streamed=lambda *_: RunResult(),
        stream_text_from=no_text,
        on_final_transcript=callback,
    )
    runtime = AgentsVoiceRuntime(
        WorkflowPipeline(workflow, "비밀 transcript"), Input, workflow=workflow
    )

    events = [event async for event in runtime.run_audio(chunks(b"\0\0"))]

    assert [(event.type, event.error_code, event.transcript) for event in events] == [
        (VoiceRuntimeEventType.ERROR, "voice_pipeline_failed", None),
    ]


async def test_runtime_cancel_cleans_final_transcript_side_channel() -> None:
    class RunResult:
        last_agent = "agent"

        def to_input_list(self) -> list[object]:
            return []

    async def no_text(_: RunResult) -> AsyncIterator[str]:
        if False:
            yield ""

    workflow = SmartDeskVoiceWorkflow(
        agent="agent", run_streamed=lambda *_: RunResult(), stream_text_from=no_text
    )
    pipeline = WorkflowPipeline(workflow, "확정된 문장", wait=True)
    input_value = Input()
    runtime = AgentsVoiceRuntime(pipeline, lambda: input_value, workflow=workflow)
    stream = runtime.run_audio(chunks(b"\0\0"))

    assert (await anext(stream)).type is VoiceRuntimeEventType.LIFECYCLE
    await stream.aclose()

    assert pipeline.result.closed is True
    assert input_value.items[-1] is None
    assert workflow._runtime_final_transcript_sink is None


def test_runtime_build_exposes_fixed_sdk_assembly() -> None:
    runtime = AgentsVoiceRuntime.build()
    pipeline = runtime._pipeline  # noqa: SLF001 - assembly boundary assertion
    assert pipeline._stt_model_name == "gpt-4o-transcribe"  # noqa: SLF001
    assert pipeline._tts_model_name == "tts-1"  # noqa: SLF001
    assert pipeline.config.stt_settings.turn_detection == {
        "type": "server_vad", "threshold": 0.5,
        "prefix_padding_ms": 300, "silence_duration_ms": 600,
    }
