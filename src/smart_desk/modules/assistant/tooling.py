"""Assistant function tool의 공통 계약과 명시적 registry다."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from time import monotonic
from typing import Protocol

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AssistantToolSpec:
    name: str
    description: str
    arguments_model: type[BaseModel]


@dataclass(frozen=True, slots=True)
class AssistantToolCall:
    call_id: str
    name: str
    arguments_json: str

    def __post_init__(self) -> None:
        if not self.call_id or not self.name:
            raise ValueError("tool call id와 name은 비어 있을 수 없습니다.")


class ToolErrorPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str


class AssistantToolOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    result: dict[str, object] | None = None
    error: ToolErrorPayload | None = None

    @model_validator(mode="after")
    def validate_envelope(self) -> AssistantToolOutput:
        if self.ok and (self.result is None or self.error is not None):
            raise ValueError("성공 tool output에는 result만 필요합니다.")
        if not self.ok and (self.result is not None or self.error is None):
            raise ValueError("실패 tool output에는 error만 필요합니다.")
        return self

    @classmethod
    def success(cls, result: dict[str, object]) -> AssistantToolOutput:
        return cls(ok=True, result=result)

    @classmethod
    def failure(cls, code: str, message: str) -> AssistantToolOutput:
        return cls(ok=False, error=ToolErrorPayload(code=code, message=message))


class AssistantToolProvider(Protocol):
    def specs(self) -> tuple[AssistantToolSpec, ...]: ...

    async def execute(
        self,
        name: str,
        arguments: BaseModel,
    ) -> AssistantToolOutput: ...


class AssistantToolRegistry:
    """정적으로 조립된 tool의 schema 검증과 provider routing을 담당한다."""

    def __init__(self, providers: tuple[AssistantToolProvider, ...]) -> None:
        specs: list[AssistantToolSpec] = []
        routes: dict[str, tuple[AssistantToolProvider, AssistantToolSpec]] = {}
        for provider in providers:
            for spec in provider.specs():
                if not spec.name:
                    raise ValueError("tool name은 비어 있을 수 없습니다.")
                if spec.name in routes:
                    raise ValueError(f"중복 tool name: {spec.name}")
                routes[spec.name] = (provider, spec)
                specs.append(spec)
        self._specs = tuple(specs)
        self._routes = routes

    def specs(self) -> tuple[AssistantToolSpec, ...]:
        return self._specs

    async def execute(self, call: AssistantToolCall) -> AssistantToolOutput:
        route = self._routes.get(call.name)
        if route is None:
            return AssistantToolOutput.failure(
                "unknown_tool",
                "요청한 기능을 사용할 수 없습니다.",
            )

        provider, spec = route
        try:
            arguments = spec.arguments_model.model_validate_json(call.arguments_json)
        except ValidationError:
            return AssistantToolOutput.failure(
                "invalid_arguments",
                "기능 실행에 필요한 값이 올바르지 않습니다.",
            )

        started_at = monotonic()
        LOGGER.info(
            "Assistant tool 실행을 시작합니다.",
            extra={
                "component": "assistant_tool",
                "event": "tool_started",
                "tool_name": call.name,
            },
        )
        try:
            output = await provider.execute(call.name, arguments)
        except asyncio.CancelledError:
            raise
        duration_ms = round((monotonic() - started_at) * 1000)
        result_code = "ok" if output.ok else output.error.code
        LOGGER.info(
            "Assistant tool 실행을 마쳤습니다.",
            extra={
                "component": "assistant_tool",
                "event": "tool_completed" if output.ok else "tool_failed",
                "tool_name": call.name,
                "result_code": result_code,
                "duration_ms": duration_ms,
            },
        )
        return output
