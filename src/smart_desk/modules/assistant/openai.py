"""OpenAI STT, Responses와 streaming TTS SDK adapter를 구현한다."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
import importlib
import io
from typing import TYPE_CHECKING, Protocol
import wave

from smart_desk.config.settings import OpenAiSettings
from smart_desk.modules.assistant.models import (
    AssistantReply,
    HistoryItem,
    OpenAiResponseStep,
)
from smart_desk.modules.assistant.tooling import AssistantToolCall, AssistantToolSpec

if TYPE_CHECKING:
    from smart_desk.modules.voice.models import AudioUtterance


MAX_TRANSCRIPTION_BYTES = 25 * 1024 * 1024


def _without_sdk_parse_metadata(value: object) -> object:
    """Responses input wire schema에 없는 parse helper 필드를 재귀적으로 제거한다."""

    if isinstance(value, dict):
        return {
            key: _without_sdk_parse_metadata(item)
            for key, item in value.items()
            if key not in {"parsed", "parsed_arguments"}
        }
    if isinstance(value, list):
        return [_without_sdk_parse_metadata(item) for item in value]
    return value


class OpenAiTurnError(Exception):
    """raw provider content를 노출하지 않는 recoverable turn 오류다."""

    def __init__(self, *, stage: str, code: str) -> None:
        super().__init__(f"{stage}:{code}")
        self.stage = stage
        self.code = code


class OpenAiGatewayPort(Protocol):
    async def transcribe(self, utterance: AudioUtterance) -> str: ...

    async def create_response_step(
        self,
        *,
        input_items: Sequence[HistoryItem],
        instructions: str,
        tools: Sequence[AssistantToolSpec],
    ) -> OpenAiResponseStep: ...

    def synthesize(self, text: str) -> AsyncIterator[bytes]: ...

    async def close(self) -> None: ...


def _validate_transcription_wav(utterance: AudioUtterance) -> None:
    # assistant package 초기화 중 voice package를 다시 여는 순환 import를 피한다.
    from smart_desk.modules.voice.models import INPUT_SAMPLE_RATE

    if len(utterance.wav) > MAX_TRANSCRIPTION_BYTES:
        raise OpenAiTurnError(stage="stt", code="audio_too_large")
    try:
        with wave.open(io.BytesIO(utterance.wav), "rb") as wav_file:
            valid = (
                wav_file.getnchannels() == 1
                and wav_file.getsampwidth() == 2
                and wav_file.getframerate() == INPUT_SAMPLE_RATE
                and wav_file.getcomptype() == "NONE"
                and wav_file.getnframes() > 0
            )
    except (EOFError, wave.Error) as error:
        raise OpenAiTurnError(stage="stt", code="audio_invalid") from error
    if not valid:
        raise OpenAiTurnError(stage="stt", code="audio_invalid")


class OpenAiGateway:
    """하나의 AsyncOpenAI client로 Voice API 세 단계를 제공한다."""

    def __init__(self, settings: OpenAiSettings) -> None:
        if settings.api_key is None:
            raise ValueError("OpenAI API key가 필요합니다.")
        package = importlib.import_module("openai")
        self._pydantic_function_tool = getattr(package, "pydantic_function_tool", None)
        self._client = package.AsyncOpenAI(
            api_key=settings.api_key.get_secret_value(),
            max_retries=0,
        )
        self._settings = settings
        self._closed = False

    async def transcribe(self, utterance: AudioUtterance) -> str:
        _validate_transcription_wav(utterance)
        wav_file = io.BytesIO(utterance.wav)
        wav_file.name = "utterance.wav"
        request: dict[str, object] = {
            "model": self._settings.transcription_model,
            "file": wav_file,
            "extra_body": {"languages": ["ko"]},
        }
        if self._settings.transcription_prompt is not None:
            request["prompt"] = self._settings.transcription_prompt
        try:
            async with asyncio.timeout(
                self._settings.transcription_timeout_seconds
            ):
                result = await self._client.audio.transcriptions.create(**request)
        except asyncio.CancelledError:
            raise
        except TimeoutError as error:
            raise OpenAiTurnError(stage="stt", code="stt_timeout") from error
        except OpenAiTurnError:
            raise
        except Exception as error:
            raise OpenAiTurnError(stage="stt", code="stt_failed") from error
        text = getattr(result, "text", None)
        if not isinstance(text, str):
            raise OpenAiTurnError(stage="stt", code="stt_result_invalid")
        return text

    def _to_openai_tool(self, spec: AssistantToolSpec) -> object:
        if self._pydantic_function_tool is not None:
            return self._pydantic_function_tool(
                spec.arguments_model,
                name=spec.name,
                description=spec.description,
            )
        return {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "strict": True,
                "parameters": spec.arguments_model.model_json_schema(),
            },
        }

    async def create_response_step(
        self,
        *,
        input_items: Sequence[HistoryItem],
        instructions: str,
        tools: Sequence[AssistantToolSpec],
    ) -> OpenAiResponseStep:
        request: dict[str, object] = {
            "model": self._settings.response_model,
            "instructions": instructions,
            "input": list(input_items),
            "reasoning": {"effort": self._settings.reasoning_effort},
            "text_format": AssistantReply,
            "store": False,
        }
        if tools:
            request.update(
                tools=[self._to_openai_tool(spec) for spec in tools],
                tool_choice="auto",
                parallel_tool_calls=False,
            )
        try:
            async with asyncio.timeout(self._settings.response_timeout_seconds):
                response = await self._client.responses.parse(**request)
        except asyncio.CancelledError:
            raise
        except TimeoutError as error:
            raise OpenAiTurnError(
                stage="responses",
                code="responses_timeout",
            ) from error
        except Exception as error:
            raise OpenAiTurnError(
                stage="responses",
                code="responses_failed",
            ) from error

        reply = getattr(response, "output_parsed", None)
        if reply is not None and not isinstance(reply, AssistantReply):
            raise OpenAiTurnError(
                stage="responses",
                code="structured_reply_invalid",
            )
        try:
            dumped_items = [
                _without_sdk_parse_metadata(
                    item.model_dump(
                        mode="json",
                        exclude_none=True,
                        warnings=False,
                    )
                )
                for item in response.output
            ]
            output_items = tuple(dumped_items)
            if any(not isinstance(item, dict) for item in output_items):
                raise TypeError("output item is not an object")
        except Exception as error:
            raise OpenAiTurnError(
                stage="responses",
                code="response_history_invalid",
            ) from error

        try:
            tool_calls = tuple(
                AssistantToolCall(
                    call_id=item["call_id"],
                    name=item["name"],
                    arguments_json=item["arguments"],
                )
                for item in output_items
                if item.get("type") == "function_call"
            )
            if any(
                not isinstance(value, str)
                for call in tool_calls
                for value in (call.call_id, call.name, call.arguments_json)
            ):
                raise TypeError("function call field is not a string")
        except Exception as error:
            raise OpenAiTurnError(
                stage="responses",
                code="function_call_invalid",
            ) from error
        if not tool_calls and reply is None:
            raise OpenAiTurnError(
                stage="responses",
                code="structured_reply_invalid",
            )

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        request_id = getattr(response, "_request_id", None)
        return OpenAiResponseStep(
            reply=reply,
            output_items=output_items,
            tool_calls=tool_calls,
            request_id=request_id if isinstance(request_id, str) else None,
            input_tokens=input_tokens if isinstance(input_tokens, int) else None,
            output_tokens=output_tokens if isinstance(output_tokens, int) else None,
        )

    async def create_response(
        self,
        *,
        history: Sequence[HistoryItem],
        user_text: str,
        instructions: str,
    ) -> OpenAiResponseStep:
        """기존 one-shot 호출자를 위한 호환 wrapper다."""

        return await self.create_response_step(
            input_items=[*history, {"role": "user", "content": user_text}],
            instructions=instructions,
            tools=(),
        )

    def synthesize(self, text: str) -> AsyncIterator[bytes]:
        if not text.strip():
            raise ValueError("TTS text는 비어 있을 수 없습니다.")

        async def stream() -> AsyncIterator[bytes]:
            try:
                async with asyncio.timeout(self._settings.speech_timeout_seconds):
                    async with self._client.audio.speech.with_streaming_response.create(
                        model=self._settings.speech_model,
                        voice=self._settings.speech_voice,
                        input=text,
                        instructions="자연스럽고 간결한 한국어로 말하세요.",
                        response_format="pcm",
                    ) as response:
                        async for chunk in response.iter_bytes(chunk_size=4_096):
                            if chunk:
                                yield chunk
            except asyncio.CancelledError:
                raise
            except TimeoutError as error:
                raise OpenAiTurnError(stage="tts", code="tts_timeout") from error
            except Exception as error:
                if isinstance(error, OpenAiTurnError):
                    raise
                raise OpenAiTurnError(stage="tts", code="tts_stream_failed") from error

        return stream()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._client.close()
