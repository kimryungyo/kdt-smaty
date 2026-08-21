"""Realtime WebSocket event 및 function-call 경계."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterable

from smart_desk.modules.assistant.agents_runtime import VoiceRuntimeEventType, VoiceRuntimeLifecycle
from smart_desk.modules.assistant.realtime_runtime import (
    RealtimeVoiceConfig,
    RealtimeVoiceRuntime,
)
from smart_desk.modules.assistant.turns import TurnStatus
from smart_desk.modules.voice.models import VoiceFatalError
from tests.unit.test_agents_tools import _context


class Transport:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self.events = iter(events)
        self.sent: list[dict[str, object]] = []
        self.closed = False

    async def send_json(self, event: dict[str, object]) -> None:
        self.sent.append(event)

    async def receive_json(self) -> dict[str, object]:
        return next(self.events)

    async def close(self) -> None:
        self.closed = True


class BlockingTransport(Transport):
    async def receive_json(self) -> dict[str, object]:
        try:
            return next(self.events)
        except StopIteration:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")


async def chunks(*items: bytes) -> AsyncIterable[bytes]:
    for item in items:
        yield item


async def failing_microphone_chunks() -> AsyncIterable[bytes]:
    yield b"\x02\x00"
    await asyncio.sleep(0)
    raise VoiceFatalError("microphone_inactive")


async def test_realtime_runtime_streams_transcript_audio_and_final_turn() -> None:
    transport = Transport([
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "불 꺼줘"},
        {"type": "response.output_audio.delta", "delta": base64.b64encode(b"\x01\x00").decode()},
        {"type": "response.done", "response": {"status": "completed", "output": []}},
    ])

    async def handler(_name: str, _arguments: dict[str, object]) -> dict[str, object]:
        raise AssertionError("tool should not run")

    runtime = RealtimeVoiceRuntime(lambda: _transport(transport), handler)
    events = [event async for event in runtime.run_audio(chunks(b"\x02\x00"))]

    assert [event.type for event in events] == [VoiceRuntimeEventType.TRANSCRIPT, VoiceRuntimeEventType.AUDIO, VoiceRuntimeEventType.LIFECYCLE]
    assert events[-1].lifecycle is VoiceRuntimeLifecycle.TURN_ENDED
    assert transport.sent[0]["type"] == "session.update"
    assert transport.sent[0]["session"]["reasoning"] == {"effort": "medium"}  # type: ignore[index]
    assert transport.sent[0]["session"]["audio"]["input"]["transcription"]["model"] == "gpt-transcribe"  # type: ignore[index]
    assert transport.sent[1] == {"type": "input_audio_buffer.append", "audio": base64.b64encode(b"\x02\x00").decode()}
    assert transport.closed is True


async def test_realtime_runtime_returns_tool_output_then_continues_response() -> None:
    transport = Transport([
        {"type": "response.done", "response": {"status": "completed", "output": [{"type": "function_call", "call_id": "call-1", "name": "turn_wled_off", "arguments": "{}"}]}},
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "불 꺼줘"},
        {"type": "response.output_audio.delta", "delta": base64.b64encode(b"\x01\x00").decode()},
        {"type": "response.done", "response": {"status": "completed", "output": []}},
    ])
    calls: list[str] = []

    async def handler(name: str, arguments: dict[str, object]) -> dict[str, object]:
        calls.append(name)
        assert arguments == {}
        return {"ok": True, "result": {"on": False}}

    events = [event async for event in RealtimeVoiceRuntime(lambda: _transport(transport), handler).run_audio(chunks(b"\x02\x00"))]

    assert calls == ["turn_wled_off"]
    assert [event.type for event in events] == [
        VoiceRuntimeEventType.TRANSCRIPT,
        VoiceRuntimeEventType.AUDIO,
        VoiceRuntimeEventType.LIFECYCLE,
    ]
    assert events[-1].lifecycle is VoiceRuntimeLifecycle.TURN_ENDED
    assert transport.sent[-2]["item"]["type"] == "function_call_output"  # type: ignore[index]
    assert transport.sent[-1] == {"type": "response.create"}


async def test_realtime_runtime_waits_for_late_transcript_after_response() -> None:
    transport = Transport([
        {"type": "response.output_audio.delta", "delta": base64.b64encode(b"\x01\x00").decode()},
        {"type": "response.done", "response": {"status": "completed", "output": []}},
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "늦은 전사"},
    ])

    async def handler(_name: str, _arguments: dict[str, object]) -> dict[str, object]:
        raise AssertionError("tool should not run")

    events = [
        event async for event in RealtimeVoiceRuntime(
            lambda: _transport(transport), handler
        ).run_audio(chunks(b"\x02\x00"))
    ]

    assert [event.type for event in events] == [
        VoiceRuntimeEventType.AUDIO,
        VoiceRuntimeEventType.TRANSCRIPT,
        VoiceRuntimeEventType.LIFECYCLE,
    ]
    assert transport.closed is True


async def test_realtime_runtime_fails_when_final_transcript_never_arrives() -> None:
    transport = BlockingTransport([
        {"type": "response.output_audio.delta", "delta": base64.b64encode(b"\x01\x00").decode()},
        {"type": "response.done", "response": {"status": "completed", "output": []}},
    ])

    async def handler(_name: str, _arguments: dict[str, object]) -> dict[str, object]:
        raise AssertionError("tool should not run")

    runtime = RealtimeVoiceRuntime(
        lambda: _transport(transport),
        handler,
        config=RealtimeVoiceConfig(transcription_grace_seconds=0.01),
    )
    events = [event async for event in runtime.run_audio(chunks(b"\x02\x00"))]

    assert [(event.type, event.error_code) for event in events] == [
        (VoiceRuntimeEventType.AUDIO, None),
        (VoiceRuntimeEventType.ERROR, "voice_pipeline_failed"),
    ]
    assert transport.closed is True


async def test_realtime_runtime_reports_microphone_failure_while_receive_is_blocked() -> None:
    transport = BlockingTransport([])

    async def handler(_name: str, _arguments: dict[str, object]) -> dict[str, object]:
        raise AssertionError("tool should not run")

    runtime = RealtimeVoiceRuntime(
        lambda: _transport(transport),
        handler,
        config=RealtimeVoiceConfig(episode_max_seconds=10),
    )
    async with asyncio.timeout(0.5):
        events = [
            event
            async for event in runtime.run_audio(failing_microphone_chunks())
        ]

    assert [(event.type, event.error_code) for event in events] == [
        (VoiceRuntimeEventType.ERROR, "microphone_inactive")
    ]
    assert transport.closed is True


async def test_realtime_runtime_rejects_invalid_pcm_before_immediate_response() -> None:
    transport = Transport([
        {
            "type": "response.output_audio.delta",
            "delta": base64.b64encode(b"\x01\x00").decode(),
        },
        {
            "type": "response.done",
            "response": {"status": "completed", "output": []},
        },
    ])

    async def handler(_name: str, _arguments: dict[str, object]) -> dict[str, object]:
        raise AssertionError("tool should not run")

    events = [
        event
        async for event in RealtimeVoiceRuntime(
            lambda: _transport(transport), handler
        ).run_audio(chunks(b"\x01"))
    ]

    assert [(event.type, event.error_code) for event in events] == [
        (VoiceRuntimeEventType.ERROR, "voice_pipeline_failed")
    ]


async def test_realtime_runtime_maps_server_vad_boundaries_to_lifecycle() -> None:
    transport = Transport([
        {"type": "input_audio_buffer.speech_started"},
        {"type": "input_audio_buffer.speech_stopped"},
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "책상 올려줘",
        },
        {
            "type": "response.output_audio.delta",
            "delta": base64.b64encode(b"\x01\x00").decode(),
        },
        {
            "type": "response.done",
            "response": {"status": "completed", "output": []},
        },
    ])

    async def handler(_name: str, _arguments: dict[str, object]) -> dict[str, object]:
        raise AssertionError("tool should not run")

    events = [
        event
        async for event in RealtimeVoiceRuntime(
            lambda: _transport(transport), handler
        ).run_audio(chunks(b"\x02\x00"))
    ]

    assert [event.lifecycle for event in events if event.lifecycle is not None] == [
        VoiceRuntimeLifecycle.SPEECH_STARTED,
        VoiceRuntimeLifecycle.PROCESSING_STARTED,
        VoiceRuntimeLifecycle.TURN_ENDED,
    ]


async def test_realtime_runtime_rejects_non_completed_response_before_tool_call() -> None:
    transport = Transport([{
        "type": "response.done",
        "response": {
            "status": "failed",
            "status_details": {"type": "failed", "reason": "provider_reason"},
            "output": [{
                "type": "function_call",
                "call_id": "call-unsafe",
                "name": "turn_wled_off",
                "arguments": "{}",
            }],
        },
    }])
    calls: list[str] = []

    async def handler(name: str, _arguments: dict[str, object]) -> dict[str, object]:
        calls.append(name)
        return {"ok": True}

    events = [
        event
        async for event in RealtimeVoiceRuntime(
            lambda: _transport(transport), handler
        ).run_audio(chunks(b"\x02\x00"))
    ]

    assert calls == []
    assert [(event.type, event.error_code) for event in events] == [
        (VoiceRuntimeEventType.ERROR, "voice_response_not_completed")
    ]


async def test_realtime_runtime_retries_one_empty_response_then_requires_audio() -> None:
    transport = Transport([
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "오늘 날씨 알려줘",
        },
        {
            "type": "response.done",
            "response": {"status": "completed", "output": []},
        },
        {
            "type": "response.output_audio.delta",
            "delta": base64.b64encode(b"\x01\x00").decode(),
        },
        {
            "type": "response.done",
            "response": {"status": "completed", "output": []},
        },
    ])

    async def handler(_name: str, _arguments: dict[str, object]) -> dict[str, object]:
        raise AssertionError("tool should not run")

    events = [
        event
        async for event in RealtimeVoiceRuntime(
            lambda: _transport(transport), handler
        ).run_audio(chunks(b"\x02\x00"))
    ]

    assert [event.type for event in events] == [
        VoiceRuntimeEventType.TRANSCRIPT,
        VoiceRuntimeEventType.AUDIO,
        VoiceRuntimeEventType.LIFECYCLE,
    ]
    retry = next(
        event
        for event in transport.sent
        if event.get("type") == "response.create" and "response" in event
    )
    assert "spoken final answer" in retry["response"]["instructions"]  # type: ignore[index]
    assert retry["response"]["tools"] == []  # type: ignore[index]


async def test_realtime_runtime_fails_after_repeated_empty_audio_response() -> None:
    transport = Transport([
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "대답해줘",
        },
        {
            "type": "response.done",
            "response": {"status": "completed", "output": []},
        },
        {
            "type": "response.done",
            "response": {"status": "completed", "output": []},
        },
    ])

    async def handler(_name: str, _arguments: dict[str, object]) -> dict[str, object]:
        raise AssertionError("tool should not run")

    events = [
        event
        async for event in RealtimeVoiceRuntime(
            lambda: _transport(transport), handler
        ).run_audio(chunks(b"\x02\x00"))
    ]

    assert [(event.type, event.error_code) for event in events] == [
        (VoiceRuntimeEventType.TRANSCRIPT, None),
        (VoiceRuntimeEventType.ERROR, "voice_response_audio_missing"),
    ]


async def test_realtime_runtime_hides_provider_error(caplog) -> None:
    transport = Transport([{
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "code": "input_audio_buffer_commit_empty",
            "param": "input_audio_buffer",
            "message": "sensitive transcript",
        },
    }])

    async def handler(_name: str, _arguments: dict[str, object]) -> dict[str, object]:
        return {"ok": True}

    with caplog.at_level(logging.WARNING):
        events = [event async for event in RealtimeVoiceRuntime(lambda: _transport(transport), handler).run_audio(chunks(b"\x02\x00"))]
    assert [(event.type, event.error_code) for event in events] == [(VoiceRuntimeEventType.ERROR, "voice_pipeline_failed")]
    record = next(record for record in caplog.records if record.event == "episode_failed")
    assert record.provider_error_type == "invalid_request_error"
    assert record.provider_error_code == "input_audio_buffer_commit_empty"
    assert record.provider_error_param == "input_audio_buffer"
    assert "sensitive transcript" not in caplog.text


async def test_realtime_service_binding_exposes_direct_tools_and_invokes_wled() -> None:
    context, _users, automation, wled, memory, turns = await _context()
    transport = Transport([
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "불 꺼줘"},
        {"type": "response.done", "response": {"status": "completed", "output": [
            {"type": "function_call", "call_id": "call-1", "name": "turn_wled_off", "arguments": "{}"},
        ]}},
        {"type": "response.output_audio.delta", "delta": base64.b64encode(b"\x01\x00").decode()},
        {"type": "response.done", "response": {"status": "completed", "output": []}},
    ])
    runtime = RealtimeVoiceRuntime.build_for_services(
        api_key="test-key", sessions=context.sessions, memory=memory, turns=turns,
        automation=automation, wled=wled, transport_factory=lambda: _transport(transport),
    )

    events = [event async for event in runtime.run_audio(chunks(b"\x02\x00"))]
    await runtime.finalize_turn("SUCCEEDED")

    session = transport.sent[0]["session"]  # type: ignore[index]
    tool_names = {tool["name"] for tool in session["tools"]}  # type: ignore[index]
    assert "turn_wled_off" in tool_names and "hold_desk" not in tool_names
    assert wled.calls[0][0] == "turn_off"
    assert events[-1].lifecycle is VoiceRuntimeLifecycle.TURN_ENDED
    assert (await turns.latest()).status is TurnStatus.SUCCEEDED  # type: ignore[union-attr]


async def test_realtime_service_binding_routes_delegate_as_a_read_only_tool() -> None:
    context, _users, automation, wled, memory, turns = await _context()
    transport = Transport([
        {"type": "response.done", "response": {"status": "completed", "output": [
            {"type": "function_call", "call_id": "call-1", "name": "delegate_complex_request", "arguments": '{"task":"내일 날씨"}'},
        ]}},
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "내일 날씨"},
        {"type": "response.output_audio.delta", "delta": base64.b64encode(b"\x01\x00").decode()},
        {"type": "response.done", "response": {"status": "completed", "output": []}},
    ])

    class Delegate:
        async def run(self, task: str, received_context: object) -> dict[str, object]:
            assert task == "내일 날씨" and received_context is not None
            return {"ok": True, "spoken_answer": "조사 결과", "sources": []}

    runtime = RealtimeVoiceRuntime.build_for_services(
        api_key="test-key", sessions=context.sessions, memory=memory, turns=turns,
        automation=automation, wled=wled, delegate=Delegate(),
        transport_factory=lambda: _transport(transport),
    )
    await anext(runtime.run_audio(chunks(b"\x02\x00")))

    schemas = transport.sent[0]["session"]["tools"]  # type: ignore[index]
    output = transport.sent[-2]["item"]["output"]  # type: ignore[index]
    assert any(item["name"] == "delegate_complex_request" for item in schemas)
    assert json.loads(output)["spoken_answer"] == "조사 결과"


async def _transport(value: Transport) -> Transport:
    return value
