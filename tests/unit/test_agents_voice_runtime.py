"""AgentsVoiceRuntime의 streaming/cancel/event 계약을 fake로 고정한다."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator

import numpy as np

from smart_desk.modules.assistant.agents_runtime import (
    AgentsVoiceRuntime,
    SmartDeskVoiceWorkflow,
    VoiceRuntimeEventType,
    VoiceRuntimeLifecycle,
)


class Context:
    def __init__(self, *, personalized: bool = True, valid: list[bool] | None = None) -> None:
        self.turn_context = type("Turn", (), {"personalized": personalized, "profile_id": "profile-a", "session": "sdk-session"})()
        self.sessions = ContextSessions(valid or [True, True, True])
        self.memory = type("Memory", (), {"search": self._search})()
        self.phases: list[str] = []
        self.searches: list[tuple[str, str]] = []
        self.followup_requested = False
        self.assistant_response = ""

    async def _search(self, profile_id: str, transcript: str) -> list[dict[str, str]]:
        self.searches.append((profile_id, transcript))
        return [{"memory": "likes tea"}]

    async def processing_started(self) -> None: self.phases.append("PROCESSING")
    async def tool_started(self) -> None: self.phases.append("TOOL")
    def append_assistant_response(self, text: str) -> None: self.assistant_response += text
    async def finish(self, status: object, *, error_code: str | None = None) -> None:
        self.phases.append(f"FINAL:{status}")

class ContextSessions:
    def __init__(self, values: list[bool]) -> None: self._values = iter(values)
    def register_run(self, _task: object) -> None: pass
    async def is_valid(self, _context: object) -> bool: return next(self._values, False)


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
    def __init__(
        self, events: list[object], *, wait: bool = False, wait_after_events: bool = False
    ) -> None:
        self.events = events
        self.wait = wait
        self.wait_after_events = wait_after_events
        self.closed = False

    async def stream(self) -> AsyncIterator[object]:
        try:
            for event in self.events:
                yield event
                if self.wait:
                    await asyncio.sleep(10)
            if self.wait_after_events:
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


async def test_runtime_closes_multiturn_sdk_stream_after_turn_ended() -> None:
    input_value = Input()
    result = Result(
        [
            Event("voice_stream_event_lifecycle", event="turn_started"),
            Event("voice_stream_event_audio", data=np.array([1], dtype=np.int16)),
            Event("voice_stream_event_lifecycle", event="turn_ended"),
        ],
        wait_after_events=True,
    )

    async with asyncio.timeout(0.2):
        events = [event async for event in AgentsVoiceRuntime(Pipeline(result), lambda: input_value).run_audio(chunks(b"\0\0"))]

    assert [event.lifecycle for event in events if event.type is VoiceRuntimeEventType.LIFECYCLE] == [
        VoiceRuntimeLifecycle.TURN_STARTED,
        VoiceRuntimeLifecycle.TURN_ENDED,
    ]
    assert result.closed is True


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

    def run_streamed(agent: object, history: object) -> RunResult:
        assert agent == "agent"
        assert history == "책상을 올려줘"
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
    assert not hasattr(workflow, "_input_history")


async def test_workflow_sdk_start_hook_has_no_unsolicited_intro() -> None:
    workflow = SmartDeskVoiceWorkflow(
        agent="agent",
        run_streamed=lambda *_: object(),
        stream_text_from=_empty_text,
    )

    assert [text async for text in workflow.on_start()] == []


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
        VoiceRuntimeEventType.TRANSCRIPT,
        VoiceRuntimeEventType.LIFECYCLE,
        VoiceRuntimeEventType.AUDIO,
    ]
    assert [event.sequence for event in events] == [1, 2, 3]
    assert events[0].transcript == "확정된 문장"
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

    assert (await anext(stream)).type is VoiceRuntimeEventType.TRANSCRIPT
    await stream.aclose()

    assert pipeline.result.closed is True
    assert input_value.items[-1] is None
    assert workflow._runtime_final_transcript_sink is None


async def test_runtime_build_exposes_fixed_sdk_assembly() -> None:
    from agents import WebSearchTool

    runtime = AgentsVoiceRuntime.build(api_key="test-key")
    try:
        pipeline = runtime._pipeline  # noqa: SLF001 - assembly boundary assertion
        workflow = runtime._workflow  # noqa: SLF001 - assembly boundary assertion
        assert workflow is not None
        assert [type(tool) for tool in workflow.agent.tools] == [WebSearchTool]
        assert pipeline._stt_model_name == "gpt-4o-transcribe"  # noqa: SLF001
        assert pipeline._tts_model_name == "gpt-4o-mini-tts"  # noqa: SLF001
        assert pipeline.config.stt_settings.turn_detection == {
            "type": "server_vad", "threshold": 0.5,
            "prefix_padding_ms": 300, "silence_duration_ms": 600,
        }
        assert pipeline.config.tts_settings.voice == "nova"
    finally:
        await runtime.stop()


async def test_workflow_passes_sdk_context_and_session_searches_personalized() -> None:
    context = Context()
    captured: dict[str, object] = {}

    def run_streamed(_agent: object, prompt: str, *, context: object, session: object) -> object:
        captured.update(prompt=prompt, context=context, session=session)
        return object()

    async def no_text(_: object) -> AsyncIterator[str]:
        yield "화면에 보일 답변"

    workflow = SmartDeskVoiceWorkflow("agent", run_streamed, no_text, context_factory=lambda: _context(context))
    await workflow.prepare_run()
    assert [item async for item in workflow.run("raw transcript")] == ["화면에 보일 답변"]
    assert captured["context"] is context and captured["session"] == "sdk-session"
    assert context.assistant_response == "화면에 보일 답변"
    assert context.searches == [("profile-a", "raw transcript")]
    assert "<profile_memory>\nlikes tea\n</profile_memory>" in captured["prompt"]


async def _context(value: Context) -> Context:
    return value


async def test_workflow_search_failure_degrades_and_session_change_drops_recall() -> None:
    context = Context(valid=[True, False])

    async def broken_search(*_: object) -> list[object]: raise RuntimeError("backend secret")
    context.memory.search = broken_search

    async def no_text(_: object) -> AsyncIterator[str]:
        if False:
            yield ""

    workflow = SmartDeskVoiceWorkflow("agent", lambda *_args, **_kwargs: object(), no_text, context_factory=lambda: _context(context))
    await workflow.prepare_run()
    assert [item async for item in workflow.run("raw transcript")] == []


async def test_workflow_never_searches_for_nonpersonalized_context() -> None:
    context = Context(personalized=False)
    workflow = SmartDeskVoiceWorkflow(
        "agent", lambda *_args, **_kwargs: object(), _empty_text,
        context_factory=lambda: _context(context),
    )
    await workflow.prepare_run()
    assert [item async for item in workflow.run("general question")] == []
    assert context.searches == []


async def test_runtime_context_failure_is_safe_and_next_run_is_allowed() -> None:
    calls = 0

    async def factory() -> Context:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("do not expose")
        return Context()

    workflow = SmartDeskVoiceWorkflow("agent", lambda *_args, **_kwargs: object(), _empty_text, context_factory=factory)
    inputs: list[Input] = []
    runtime = AgentsVoiceRuntime(Pipeline(Result([])), lambda: inputs.append(Input()) or inputs[-1], workflow=workflow)
    first = [event async for event in runtime.run_audio(chunks(b"\0\0"))]
    second = [event async for event in runtime.run_audio(chunks(b"\0\0"))]
    assert [(event.type, event.error_code) for event in first] == [(VoiceRuntimeEventType.ERROR, "voice_pipeline_failed")]
    assert second == []
    assert runtime._run_in_progress is False and workflow._active_context is None  # noqa: SLF001
    assert inputs[0].items == [None]


async def _empty_text(_: object) -> AsyncIterator[str]:
    if False:
        yield ""


async def test_turn_ended_defers_success_until_voice_playback_finalizes() -> None:
    context = Context()
    context.followup_requested = True

    class EndingPipeline(WorkflowPipeline):
        async def run(self, _: Input) -> Result:
            async def stream() -> AsyncIterator[object]:
                async for _text in self.workflow.run(self.transcript):
                    pass
                yield Event("voice_stream_event_lifecycle", event="turn_started")
                yield Event("voice_stream_event_audio", data=np.array([7], dtype=np.int16))
                yield Event("voice_stream_event_lifecycle", event="turn_ended")

            self.result.stream = stream  # type: ignore[method-assign]
            return self.result

    async def tool_text(_: object) -> AsyncIterator[str]:
        await context.tool_started()
        if False:
            yield ""

    workflow = SmartDeskVoiceWorkflow("agent", lambda *_args, **_kwargs: object(), tool_text, context_factory=lambda: _context(context))
    runtime = AgentsVoiceRuntime(EndingPipeline(workflow, "final"), Input, workflow=workflow)
    events = [event async for event in runtime.run_audio(chunks(b"\0\0"))]
    assert events[-1].followup_requested is True
    assert context.phases == ["PROCESSING", "TOOL"]
    await runtime.finalize_turn("SUCCEEDED")
    assert context.phases == ["PROCESSING", "TOOL", "FINAL:SUCCEEDED"]


async def test_runtime_consumer_cancellation_finalizes_current_turn_cancelled() -> None:
    context = Context()
    workflow = SmartDeskVoiceWorkflow("agent", lambda *_args, **_kwargs: object(), _empty_text, context_factory=lambda: _context(context))
    pipeline = WorkflowPipeline(workflow, "final", wait=True)
    stream = AgentsVoiceRuntime(pipeline, Input, workflow=workflow).run_audio(chunks(b"\0\0"))
    await anext(stream)
    await stream.aclose()
    assert context.phases == ["PROCESSING", "FINAL:CANCELLED"]


async def test_session_ended_cancels_unfinished_turn_without_success() -> None:
    context = Context()
    workflow = SmartDeskVoiceWorkflow("agent", lambda *_args, **_kwargs: object(), _empty_text, context_factory=lambda: _context(context))
    pipeline = Pipeline(Result([Event("voice_stream_event_lifecycle", event="session_ended")]))
    events = [event async for event in AgentsVoiceRuntime(pipeline, Input, workflow=workflow).run_audio(chunks(b"\0\0"))]
    assert events[0].lifecycle.value == "session_ended"
    assert context.phases == ["FINAL:CANCELLED"]


async def test_runtime_error_after_a_turn_marks_failed_not_succeeded() -> None:
    context = Context()
    workflow = SmartDeskVoiceWorkflow("agent", lambda *_args, **_kwargs: object(), _empty_text, context_factory=lambda: _context(context))
    pipeline = Pipeline(Result([Event("voice_stream_event_lifecycle", event="turn_started"), Event("voice_stream_event_error")]))
    events = [event async for event in AgentsVoiceRuntime(pipeline, Input, workflow=workflow).run_audio(chunks(b"\0\0"))]
    assert [(event.type, event.error_code) for event in events] == [
        (VoiceRuntimeEventType.LIFECYCLE, None), (VoiceRuntimeEventType.ERROR, "voice_pipeline_failed")
    ]
    assert context.phases == ["FINAL:FAILED"]


async def test_runtime_exhaustion_after_final_transcript_marks_turn_failed() -> None:
    context = Context()
    workflow = SmartDeskVoiceWorkflow("agent", lambda *_args, **_kwargs: object(), _empty_text, context_factory=lambda: _context(context))
    pipeline = WorkflowPipeline(workflow, "확정된 문장")

    events = [event async for event in AgentsVoiceRuntime(pipeline, Input, workflow=workflow).run_audio(chunks(b"\0\0"))]

    assert [event.type for event in events] == [VoiceRuntimeEventType.TRANSCRIPT, VoiceRuntimeEventType.LIFECYCLE, VoiceRuntimeEventType.AUDIO]
    assert context.phases == ["PROCESSING", "FINAL:FAILED"]


async def test_finalize_turn_retries_after_context_finish_failure_and_is_idempotent() -> None:
    class FailingContext:
        def __init__(self) -> None: self.calls = 0

        async def finish(self, _status: object, *, error_code: str | None = None) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("store unavailable")

    runtime = AgentsVoiceRuntime(Pipeline(Result([])), Input)
    context = FailingContext()
    runtime._turn_context = context  # noqa: SLF001 - terminal retry contract

    import pytest

    with pytest.raises(RuntimeError):
        await runtime.finalize_turn("FAILED", error_code="voice_pipeline_failed")
    await runtime.finalize_turn("FAILED", error_code="voice_pipeline_failed")
    await runtime.finalize_turn("FAILED", error_code="voice_pipeline_failed")
    assert context.calls == 2
