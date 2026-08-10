"""OpenAI SDK request 조립과 오류 정규화 테스트."""

import asyncio
from types import SimpleNamespace

import pytest

from smart_desk.config.settings import OpenAiSettings
from smart_desk.modules.assistant.models import AssistantReply
from smart_desk.modules.assistant.openai import OpenAiGateway, OpenAiTurnError
from smart_desk.modules.voice.audio import build_wav


class FakeTranscriptions:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    async def create(self, **request: object):
        self.requests.append(request)
        return SimpleNamespace(text="테스트 전사")


class FakeOutputItem:
    def __init__(self, value: dict[str, object]) -> None:
        self.value = value
        self.dump_calls: list[dict[str, object]] = []

    def model_dump(self, **kwargs: object) -> dict[str, object]:
        self.dump_calls.append(kwargs)
        return dict(self.value)


class FakeResponses:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.items = [
            FakeOutputItem(
                {
                    "type": "reasoning",
                    "encrypted_content": "reasoning-canary",
                }
            ),
            FakeOutputItem({"type": "message", "role": "assistant"}),
        ]

    async def parse(self, **request: object):
        self.requests.append(request)
        return SimpleNamespace(
            output_parsed=AssistantReply(spoken_text="응답입니다."),
            output=self.items,
            usage=SimpleNamespace(input_tokens=10, output_tokens=4),
            _request_id="req_test",
        )


class FakeStreamResponse:
    async def iter_bytes(self, *, chunk_size: int):
        assert chunk_size == 4_096
        yield b"\x01\x02"
        yield b""
        yield b"\x03\x04"


class FakeStreamContext:
    def __init__(self) -> None:
        self.entered = 0
        self.exited = 0

    async def __aenter__(self):
        self.entered += 1
        return FakeStreamResponse()

    async def __aexit__(self, *_args: object) -> None:
        self.exited += 1


class FakeSpeechCreate:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.context = FakeStreamContext()

    def __call__(self, **request: object):
        self.requests.append(request)
        return self.context


class FakeClient:
    def __init__(self) -> None:
        self.transcriptions = FakeTranscriptions()
        self.responses = FakeResponses()
        self.speech_create = FakeSpeechCreate()
        self.audio = SimpleNamespace(
            transcriptions=self.transcriptions,
            speech=SimpleNamespace(
                with_streaming_response=SimpleNamespace(create=self.speech_create)
            ),
        )
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1


def make_gateway(monkeypatch: pytest.MonkeyPatch, **settings: object):
    client = FakeClient()
    constructor_args: list[dict[str, object]] = []

    def constructor(**kwargs: object):
        constructor_args.append(kwargs)
        return client

    monkeypatch.setattr(
        "smart_desk.modules.assistant.openai.importlib.import_module",
        lambda _name: SimpleNamespace(AsyncOpenAI=constructor),
    )
    gateway = OpenAiGateway(OpenAiSettings(api_key="test-secret", **settings))
    return gateway, client, constructor_args


async def test_transcription_uses_named_memory_wav_and_language_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, client, constructor_args = make_gateway(monkeypatch)
    utterance = build_wav([b"\0" * 2_560])

    result = await gateway.transcribe(utterance)

    assert result == "테스트 전사"
    assert constructor_args == [{"api_key": "test-secret", "max_retries": 0}]
    request = client.transcriptions.requests[0]
    assert request["model"] == "gpt-transcribe"
    assert request["extra_body"] == {"languages": ["ko"]}
    assert "prompt" not in request
    assert request["file"].name == "utterance.wav"  # type: ignore[union-attr]


async def test_transcription_includes_configured_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, client, _constructor_args = make_gateway(
        monkeypatch,
        transcription_prompt="스마트 데스크",
    )

    await gateway.transcribe(build_wav([b"\0" * 2_560]))

    assert client.transcriptions.requests[0]["prompt"] == "스마트 데스크"


async def test_responses_parse_replays_all_items_and_disables_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, client, _constructor_args = make_gateway(monkeypatch)
    history = [{"type": "reasoning", "encrypted_content": "old"}]

    turn = await gateway.create_response(
        history=history,
        user_text="질문",
        instructions="지침",
    )

    request = client.responses.requests[0]
    assert request["input"] == [
        *history,
        {"role": "user", "content": "질문"},
    ]
    assert request["store"] is False
    assert request["text_format"] is AssistantReply
    assert request["reasoning"] == {"effort": "low"}
    assert turn.output_items[0]["encrypted_content"] == "reasoning-canary"
    assert turn.request_id == "req_test"
    assert turn.input_tokens == 10
    assert client.responses.items[0].dump_calls == [
        {"mode": "json", "exclude_none": True}
    ]


async def test_tts_is_lazy_streaming_pcm_and_closes_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, client, _constructor_args = make_gateway(monkeypatch)
    stream = gateway.synthesize("안녕하세요.")

    assert client.speech_create.requests == []
    assert [chunk async for chunk in stream] == [b"\x01\x02", b"\x03\x04"]
    assert client.speech_create.requests == [
        {
            "model": "gpt-4o-mini-tts",
            "voice": "marin",
            "input": "안녕하세요.",
            "instructions": "자연스럽고 간결한 한국어로 말하세요.",
            "response_format": "pcm",
        }
    ]
    assert client.speech_create.context.exited == 1


async def test_response_timeout_is_content_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, client, _constructor_args = make_gateway(
        monkeypatch,
        response_timeout_seconds=0.01,
    )

    async def wait_forever(**_request: object):
        await asyncio.Future()

    client.responses.parse = wait_forever  # type: ignore[method-assign]

    with pytest.raises(OpenAiTurnError, match="responses_timeout") as captured:
        await gateway.create_response(history=[], user_text="canary", instructions="지침")

    assert "canary" not in str(captured.value)


async def test_close_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway, client, _constructor_args = make_gateway(monkeypatch)

    await gateway.close()
    await gateway.close()

    assert client.close_count == 1
