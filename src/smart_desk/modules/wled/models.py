"""WLED의 작은 공개 모델과 HTTP 요청 계약이다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WledMode(StrEnum):
    OFF = "OFF"
    SOLID = "SOLID"
    EFFECT = "EFFECT"
    MIXED = "MIXED"


class WledStatus(StrEnum):
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"
    ONLINE = "ONLINE"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class WledCatalogItem:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class WledCapabilities:
    device_name: str
    firmware_version: str
    effects: tuple[WledCatalogItem, ...]
    palettes: tuple[WledCatalogItem, ...]
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class WledSnapshot:
    status: WledStatus
    on: bool | None
    mode: WledMode | None
    color: str | None
    effect_id: int | None
    effect_name: str | None
    palette_id: int | None
    speed: int | None
    intensity: int | None
    observed_at: datetime | None
    last_error: str | None


def _camel(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(part.title() for part in rest)


class WledApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True, extra="forbid")


class SolidControlRequest(WledApiModel):
    action: Literal["SOLID"]
    color: str

    @field_validator("color")
    @classmethod
    def normalize_color(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not re.fullmatch(r"[0-9A-F]{6}", normalized):
            raise ValueError("색상은 6자리 RGB hexadecimal 값이어야 합니다.")
        return normalized


class EffectControlRequest(WledApiModel):
    action: Literal["EFFECT"]
    effect_id: int = Field(ge=1)
    palette_id: int = Field(default=0, ge=0)
    speed: int = Field(default=128, ge=0, le=255)
    intensity: int = Field(default=128, ge=0, le=255)
    color: str | None = None

    @field_validator("color")
    @classmethod
    def normalize_optional_color(cls, value: str | None) -> str | None:
        return SolidControlRequest(action="SOLID", color=value).color if value is not None else None


class OffControlRequest(WledApiModel):
    action: Literal["OFF"]


ControlRequest = Annotated[
    SolidControlRequest | EffectControlRequest | OffControlRequest,
    Field(discriminator="action"),
]


class WledCatalogItemResponse(WledApiModel):
    id: int
    name: str


class WledCapabilitiesResponse(WledApiModel):
    device_name: str
    firmware_version: str
    effects: list[WledCatalogItemResponse]
    palettes: list[WledCatalogItemResponse]
    observed_at: datetime


class WledSnapshotResponse(WledApiModel):
    status: WledStatus
    on: bool | None
    mode: WledMode | None
    color: str | None
    effect_id: int | None
    effect_name: str | None
    palette_id: int | None
    speed: int | None
    intensity: int | None
    observed_at: datetime | None
    last_error: str | None


def snapshot_response(snapshot: WledSnapshot) -> WledSnapshotResponse:
    return WledSnapshotResponse(**{field: getattr(snapshot, field) for field in WledSnapshot.__dataclass_fields__})


def capabilities_response(capabilities: WledCapabilities) -> WledCapabilitiesResponse:
    return WledCapabilitiesResponse(
        device_name=capabilities.device_name,
        firmware_version=capabilities.firmware_version,
        effects=[WledCatalogItemResponse(id=item.id, name=item.name) for item in capabilities.effects],
        palettes=[WledCatalogItemResponse(id=item.id, name=item.name) for item in capabilities.palettes],
        observed_at=capabilities.observed_at,
    )
