"""WLED domain API를 Assistant function tool로 노출한다."""

from __future__ import annotations

from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from smart_desk.modules.assistant.tooling import (
    AssistantToolOutput,
    AssistantToolSpec,
)
from smart_desk.modules.wled.client import (
    WledClient,
    WledDisabledError,
    WledNotStartedError,
    WledProtocolError,
    WledUnavailableError,
    WledUnsupportedValueError,
)
from smart_desk.modules.wled.models import WledCapabilities, WledSnapshot


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetWledBrightnessArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brightness_percent: int = Field(ge=0, le=100)

    @field_validator("brightness_percent", mode="before")
    @classmethod
    def reject_bool(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("brightness percent cannot be boolean")
        return value


class SetWledColorArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    color_hex: Annotated[str, StringConstraints(pattern=r"^[0-9A-Fa-f]{6}$")]

    @field_validator("color_hex")
    @classmethod
    def normalize_color(cls, value: str) -> str:
        return value.upper()


class SetWledEffectArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effect_id: int = Field(ge=1)
    palette_id: int = Field(ge=0)
    speed: int = Field(ge=0, le=255)
    intensity: int = Field(ge=0, le=255)
    color_hex: Annotated[
        str | None,
        StringConstraints(pattern=r"^[0-9A-Fa-f]{6}$"),
    ]

    @field_validator("effect_id", "palette_id", "speed", "intensity", mode="before")
    @classmethod
    def reject_bool(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("numeric WLED arguments cannot be boolean")
        return value

    @field_validator("color_hex")
    @classmethod
    def normalize_optional_color(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None


def percent_to_wled_brightness(percent: int) -> int:
    return round(percent * 255 / 100)


def wled_brightness_to_percent(brightness: int) -> int:
    return round(brightness * 100 / 255)


def _snapshot_result(snapshot: WledSnapshot) -> dict[str, object]:
    result: dict[str, object] = {
        "status": snapshot.status.value,
        "on": snapshot.on,
        "brightness": snapshot.brightness,
        "brightness_percent": (
            wled_brightness_to_percent(snapshot.brightness)
            if snapshot.brightness is not None
            else None
        ),
        "mode": snapshot.mode.value if snapshot.mode is not None else None,
        "color": snapshot.color,
        "effect_id": snapshot.effect_id,
        "effect_name": snapshot.effect_name,
        "palette_id": snapshot.palette_id,
        "speed": snapshot.speed,
        "intensity": snapshot.intensity,
    }
    if snapshot.observed_at is not None:
        result["observed_at"] = snapshot.observed_at.isoformat()
    return result


def _capabilities_result(capabilities: WledCapabilities) -> dict[str, object]:
    return {
        "device_name": capabilities.device_name,
        "firmware_version": capabilities.firmware_version,
        "effects": [
            {"id": item.id, "name": item.name} for item in capabilities.effects
        ],
        "palettes": [
            {"id": item.id, "name": item.name} for item in capabilities.palettes
        ],
        "observed_at": capabilities.observed_at.isoformat(),
    }


class WledAssistantTools:
    def __init__(self, client: WledClient) -> None:
        self._client = client
        self._specs = (
            AssistantToolSpec(
                "get_wled_state",
                "현재 단일 WLED 조명의 최신 상태를 조회할 때 사용한다.",
                NoArguments,
            ),
            AssistantToolSpec(
                "turn_off_wled",
                "사용자가 조명을 끄라고 할 때만 사용한다. 밝기 0퍼센트 요청에는 사용하지 않는다.",
                NoArguments,
            ),
            AssistantToolSpec(
                "turn_on_wled",
                "사용자가 조명을 켜라고 할 때 사용한다. 기존 밝기, 색상과 effect를 유지한다.",
                NoArguments,
            ),
            AssistantToolSpec(
                "set_wled_brightness",
                "WLED 조명의 밝기를 0~100퍼센트로 변경한다. 단순히 끄라는 요청에는 사용하지 않는다.",
                SetWledBrightnessArguments,
            ),
            AssistantToolSpec(
                "set_wled_color",
                "WLED 전체 조명을 6자리 RGB hexadecimal 단색으로 변경한다.",
                SetWledColorArguments,
            ),
            AssistantToolSpec(
                "get_wled_capabilities",
                "지원 effect 또는 palette를 모를 때 목록을 먼저 조회한다.",
                NoArguments,
            ),
            AssistantToolSpec(
                "set_wled_effect",
                "지원 여부를 확인한 WLED effect와 palette를 적용한다. 모르면 capabilities를 먼저 조회한다.",
                SetWledEffectArguments,
            ),
        )

    def specs(self) -> tuple[AssistantToolSpec, ...]:
        return self._specs

    async def execute(
        self,
        name: str,
        arguments: BaseModel,
    ) -> AssistantToolOutput:
        try:
            if name == "get_wled_state":
                result = _snapshot_result(await self._client.refresh_state())
            elif name == "turn_off_wled":
                result = _snapshot_result(await self._client.turn_off())
            elif name == "turn_on_wled":
                result = _snapshot_result(await self._client.turn_on())
            elif name == "set_wled_brightness":
                assert isinstance(arguments, SetWledBrightnessArguments)
                snapshot = await self._client.set_brightness(
                    percent_to_wled_brightness(arguments.brightness_percent)
                )
                result = _snapshot_result(snapshot)
                result["requested_brightness_percent"] = arguments.brightness_percent
            elif name == "set_wled_color":
                assert isinstance(arguments, SetWledColorArguments)
                result = _snapshot_result(
                    await self._client.set_solid(arguments.color_hex)
                )
            elif name == "get_wled_capabilities":
                result = _capabilities_result(
                    await self._client.refresh_capabilities()
                )
            elif name == "set_wled_effect":
                assert isinstance(arguments, SetWledEffectArguments)
                result = _snapshot_result(
                    await self._client.set_effect(
                        arguments.effect_id,
                        palette_id=arguments.palette_id,
                        speed=arguments.speed,
                        intensity=arguments.intensity,
                        color=arguments.color_hex,
                    )
                )
            else:
                raise RuntimeError("registry가 알 수 없는 WLED tool을 전달했습니다.")
        except WledDisabledError:
            return AssistantToolOutput.failure(
                "wled_disabled", "이 실행에서는 조명 기능을 사용할 수 없습니다."
            )
        except WledNotStartedError:
            return AssistantToolOutput.failure(
                "wled_not_ready", "조명 기능이 아직 준비되지 않았습니다."
            )
        except WledUnavailableError:
            return AssistantToolOutput.failure(
                "wled_unavailable", "지금은 조명에 연결할 수 없습니다."
            )
        except WledUnsupportedValueError:
            return AssistantToolOutput.failure(
                "wled_unsupported_value", "조명이 지원하지 않는 설정입니다."
            )
        except WledProtocolError:
            return AssistantToolOutput.failure(
                "wled_response_invalid", "조명에 설정이 적용됐는지 확인할 수 없습니다."
            )
        return AssistantToolOutput.success(result)
