"""WLED Assistant provider의 단위 변환과 안전한 오류 테스트."""

from datetime import UTC, datetime

import pytest

from smart_desk.modules.assistant.tooling import AssistantToolCall, AssistantToolRegistry
from smart_desk.modules.assistant.wled_tools import (
    WledAssistantTools,
    percent_to_wled_brightness,
)
from smart_desk.modules.wled.client import WledProtocolError, WledUnavailableError
from smart_desk.modules.wled.models import WledMode, WledSnapshot, WledStatus


def snapshot(*, brightness: int = 128, on: bool = True) -> WledSnapshot:
    return WledSnapshot(
        status=WledStatus.ONLINE,
        on=on,
        brightness=brightness,
        mode=WledMode.SOLID if on else WledMode.OFF,
        color="0000FF" if on else None,
        effect_id=0 if on else None,
        effect_name="Solid" if on else None,
        palette_id=0 if on else None,
        speed=128 if on else None,
        intensity=128 if on else None,
        observed_at=datetime(2026, 8, 11, tzinfo=UTC),
        last_error="PRIVATE-DEVICE-ERROR",
    )


class FakeWled:
    def __init__(self) -> None:
        self.brightness_values: list[int] = []
        self.off_count = 0
        self.on_count = 0
        self.error: Exception | None = None

    def _result(self) -> WledSnapshot:
        if self.error is not None:
            raise self.error
        return snapshot()

    async def refresh_state(self) -> WledSnapshot:
        return self._result()

    async def turn_off(self) -> WledSnapshot:
        self.off_count += 1
        return snapshot(brightness=128, on=False)

    async def turn_on(self) -> WledSnapshot:
        self.on_count += 1
        return snapshot(brightness=128, on=True)

    async def set_brightness(self, value: int) -> WledSnapshot:
        self.brightness_values.append(value)
        return snapshot(brightness=value)


@pytest.mark.parametrize("percent,value", [(0, 0), (1, 3), (50, 128), (100, 255)])
def test_percent_conversion(percent: int, value: int) -> None:
    assert percent_to_wled_brightness(percent) == value


async def test_brightness_uses_255_unit_without_turning_off() -> None:
    client = FakeWled()
    registry = AssistantToolRegistry((WledAssistantTools(client),))  # type: ignore[arg-type]

    output = await registry.execute(
        AssistantToolCall(
            "call-1", "set_wled_brightness", '{"brightness_percent":50}'
        )
    )
    await registry.execute(
        AssistantToolCall(
            "call-2", "set_wled_brightness", '{"brightness_percent":0}'
        )
    )

    assert client.brightness_values == [128, 0]
    assert client.off_count == 0
    assert output.result is not None
    assert output.result["requested_brightness_percent"] == 50
    assert output.result["brightness"] == 128
    assert "last_error" not in output.result


async def test_turn_off_uses_only_off_method() -> None:
    client = FakeWled()
    output = await AssistantToolRegistry(
        (WledAssistantTools(client),)  # type: ignore[arg-type]
    ).execute(AssistantToolCall("call-1", "turn_off_wled", "{}"))

    assert output.ok is True
    assert client.off_count == 1
    assert client.brightness_values == []


async def test_turn_on_preserves_settings_through_dedicated_method() -> None:
    client = FakeWled()
    output = await AssistantToolRegistry(
        (WledAssistantTools(client),)  # type: ignore[arg-type]
    ).execute(AssistantToolCall("call-1", "turn_on_wled", "{}"))

    assert output.ok is True
    assert client.on_count == 1
    assert client.off_count == 0
    assert client.brightness_values == []


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (WledUnavailableError("http://private/raw body"), "wled_unavailable"),
        (WledProtocolError("PRIVATE-PROTOCOL-DETAIL"), "wled_response_invalid"),
    ],
)
async def test_errors_are_mapped_without_raw_detail(error: Exception, code: str) -> None:
    client = FakeWled()
    client.error = error
    output = await AssistantToolRegistry(
        (WledAssistantTools(client),)  # type: ignore[arg-type]
    ).execute(AssistantToolCall("call-1", "get_wled_state", "{}"))

    rendered = output.model_dump_json()
    assert output.error is not None
    assert output.error.code == code
    assert "PRIVATE" not in rendered
    assert "http://" not in rendered
