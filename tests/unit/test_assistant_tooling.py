"""Assistant tool registry의 validation과 routing 계약 테스트."""

import asyncio

from pydantic import BaseModel, ConfigDict, Field, field_validator
import pytest

from smart_desk.modules.assistant.tooling import (
    AssistantToolCall,
    AssistantToolOutput,
    AssistantToolRegistry,
    AssistantToolSpec,
)


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int = Field(ge=0)

    @field_validator("value", mode="before")
    @classmethod
    def reject_bool(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("bool is not an integer argument")
        return value


class Provider:
    def __init__(self, name: str = "sample_tool") -> None:
        self.name = name
        self.arguments: list[BaseModel] = []
        self.cancel = False

    def specs(self) -> tuple[AssistantToolSpec, ...]:
        return (AssistantToolSpec(self.name, "sample", Arguments),)

    async def execute(self, name: str, arguments: BaseModel) -> AssistantToolOutput:
        assert name == self.name
        self.arguments.append(arguments)
        if self.cancel:
            raise asyncio.CancelledError
        return AssistantToolOutput.success({"value": arguments.value})  # type: ignore[attr-defined]


async def test_registry_validates_and_routes_arguments() -> None:
    provider = Provider()
    registry = AssistantToolRegistry((provider,))

    output = await registry.execute(
        AssistantToolCall("call-1", "sample_tool", '{"value":3}')
    )

    assert registry.specs() == provider.specs()
    assert output == AssistantToolOutput.success({"value": 3})
    assert provider.arguments == [Arguments(value=3)]


@pytest.mark.parametrize(
    "arguments",
    ["not-json", '{"value":true}', '{"value":1,"extra":2}', '{"value":-1}'],
)
async def test_invalid_arguments_do_not_reach_provider(arguments: str) -> None:
    provider = Provider()
    output = await AssistantToolRegistry((provider,)).execute(
        AssistantToolCall("call-1", "sample_tool", arguments)
    )

    assert output.error is not None
    assert output.error.code == "invalid_arguments"
    assert provider.arguments == []


async def test_unknown_tool_is_safe_and_does_not_call_provider() -> None:
    provider = Provider()
    output = await AssistantToolRegistry((provider,)).execute(
        AssistantToolCall("call-1", "missing_tool", "{}")
    )

    assert output.error is not None
    assert output.error.code == "unknown_tool"
    assert provider.arguments == []


def test_duplicate_and_empty_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="중복"):
        AssistantToolRegistry((Provider(), Provider()))
    with pytest.raises(ValueError, match="비어"):
        AssistantToolRegistry((Provider(""),))


async def test_cancellation_is_propagated() -> None:
    provider = Provider()
    provider.cancel = True
    with pytest.raises(asyncio.CancelledError):
        await AssistantToolRegistry((provider,)).execute(
            AssistantToolCall("call-1", "sample_tool", '{"value":1}')
        )
