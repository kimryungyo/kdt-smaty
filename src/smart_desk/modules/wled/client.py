"""WLED JSON API를 전체 조명 동작으로 제한해 감싸는 client다."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from smart_desk.config.settings import WledSettings
from smart_desk.modules.wled.models import (
    WledCapabilities,
    WledCatalogItem,
    WledMode,
    WledSnapshot,
    WledStatus,
)


LOGGER = logging.getLogger(__name__)


class WledError(RuntimeError): pass
class WledDisabledError(WledError): pass
class WledNotStartedError(WledError):
    pass


class WledUnavailableError(WledError):
    pass


class WledProtocolError(WledError):
    pass


class WledUnsupportedValueError(WledError):
    pass


class WledSessionMismatchError(WledError):
    pass


class WledClient:
    def __init__(
        self,
        settings: WledSettings,
        *,
        session_validator: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None
        self._request_lock = asyncio.Lock()
        self._snapshot = WledSnapshot(WledStatus.UNKNOWN, None, None, None, None, None, None, None, None, None, None, None)
        self._capabilities: WledCapabilities | None = None
        self._session_validator = session_validator

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._settings.base_url, timeout=self._settings.timeout_seconds)

    async def stop(self) -> None:
        async with self._request_lock:
            if self._client is not None:
                await self._client.aclose()
                self._client = None

    def get_snapshot(self) -> WledSnapshot: return self._snapshot
    def get_capabilities(self) -> WledCapabilities | None: return self._capabilities

    async def refresh_capabilities(self) -> WledCapabilities:
        async with self._request_lock:
            try:
                payload = await self._get_json("/json")
                capabilities = self._parse_capabilities(payload)
            except WledError as error:
                self._record_failure(error)
                raise
            self._capabilities = capabilities
            LOGGER.info("WLED capabilities loaded", extra={"component": "wled", "event": "wled_capabilities_loaded"})
            return capabilities

    async def refresh_state(self) -> WledSnapshot:
        async with self._request_lock:
            try:
                snapshot = self._snapshot_from_state(await self._get_json("/json/state"))
            except WledError as error:
                self._record_failure(error)
                raise
            self._snapshot = snapshot
            return snapshot

    async def turn_off(self, *, expected_session_id: str | None = None) -> WledSnapshot:
        async with self._request_lock:
            try:
                response = await self._post_state({"on": False, "v": True}, expected_session_id)
                if response.get("on") is not False:
                    raise WledProtocolError("WLED가 전원 끄기를 확인하지 못했습니다.")
                self._snapshot = self._snapshot_from_state(response)
                return self._snapshot
            except WledError as error:
                self._record_failure(error)
                raise

    async def turn_on(self, *, expected_session_id: str | None = None) -> WledSnapshot:
        """현재 밝기와 segment 설정을 유지하면서 master 전원을 켠다."""

        async with self._request_lock:
            try:
                response = await self._post_state({"on": True, "v": True}, expected_session_id)
                if response.get("on") is not True:
                    raise WledProtocolError("WLED가 전원 켜기를 확인하지 못했습니다.")
                self._snapshot = self._snapshot_from_state(response)
                return self._snapshot
            except WledError as error:
                self._record_failure(error)
                raise

    async def set_brightness(self, brightness: int, *, expected_session_id: str | None = None) -> WledSnapshot:
        """WLED master 밝기만 바꾸며 전원과 segment 설정은 유지한다."""

        if isinstance(brightness, bool) or not isinstance(brightness, int) or not 0 <= brightness <= 255:
            raise WledProtocolError("밝기는 0에서 255 사이의 정수여야 합니다.")
        async with self._request_lock:
            try:
                response = await self._post_state({"bri": brightness, "v": True}, expected_session_id)
                if response.get("bri") != brightness:
                    raise WledProtocolError("WLED가 요청한 밝기를 확인하지 못했습니다.")
                self._snapshot = self._snapshot_from_state(response)
                self._log_applied("BRIGHTNESS", brightness=brightness)
                return self._snapshot
            except WledError as error:
                self._record_failure(error)
                raise

    async def set_solid(self, color: str, *, expected_session_id: str | None = None) -> WledSnapshot:
        normalized = self._normalize_color(color)
        async with self._request_lock:
            try:
                state = await self._get_json("/json/state")
                segments = self._valid_segments(state)
                if state["on"] is False:
                    await self._post_state({"on": True, "v": True}, expected_session_id)
                rgb = self._rgb(normalized)
                expected = {segment["id"]: {"fx": 0, "pal": 0, "col": rgb} for segment in segments}
                response = await self._post_state({"seg": [{"id": item_id, "fx": 0, "pal": 0, "col": [rgb]} for item_id in expected], "v": True}, expected_session_id)
                self._verify_response(response, expected)
                self._snapshot = self._snapshot_from_state(response)
                self._log_applied("SOLID", segment_count=len(segments))
                return self._snapshot
            except WledError as error:
                self._record_failure(error)
                raise

    async def set_effect(self, effect_id: int, *, palette_id: int = 0, speed: int = 128, intensity: int = 128, color: str | None = None, expected_session_id: str | None = None) -> WledSnapshot:
        async with self._request_lock:
            try:
                await self._validate_effect(effect_id, palette_id)
                state = await self._get_json("/json/state")
                segments = self._valid_segments(state)
                if state["on"] is False:
                    await self._post_state({"on": True, "v": True}, expected_session_id)
                normalized = self._normalize_color(color) if color is not None else None
                rgb = self._rgb(normalized) if normalized else None
                expected = {segment["id"]: {"fx": effect_id, "pal": palette_id, "sx": speed, "ix": intensity, **({"col": rgb} if rgb else {})} for segment in segments}
                command_segments = []
                for item_id, values in expected.items():
                    command = {"id": item_id, **{key: value for key, value in values.items() if key != "col"}}
                    if "col" in values: command["col"] = [values["col"]]
                    command_segments.append(command)
                response = await self._post_state({"seg": command_segments, "v": True}, expected_session_id)
                self._verify_response(response, expected)
                self._snapshot = self._snapshot_from_state(response)
                self._log_applied("EFFECT", effect_id=effect_id, palette_id=palette_id, segment_count=len(segments))
                return self._snapshot
            except WledError as error:
                self._record_failure(error)
                raise

    async def _validate_effect(self, effect_id: int, palette_id: int) -> None:
        capabilities = self._capabilities or self._parse_capabilities(await self._get_json("/json"))
        self._capabilities = capabilities
        if effect_id in {item.id for item in capabilities.effects} and palette_id in {item.id for item in capabilities.palettes}: return
        capabilities = self._parse_capabilities(await self._get_json("/json"))
        self._capabilities = capabilities
        if effect_id not in {item.id for item in capabilities.effects} or palette_id not in {item.id for item in capabilities.palettes}:
            raise WledUnsupportedValueError("WLED가 요청한 effect 또는 palette를 지원하지 않습니다.")

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None: raise WledNotStartedError("WLED client가 시작되지 않았습니다.")
        return self._client

    async def _get_json(self, path: str) -> dict[str, Any]:
        try:
            response = await self._require_client().get(path)
            return self._response_object(response)
        except httpx.HTTPError as error:
            raise WledUnavailableError("WLED 장치에 연결할 수 없습니다.") from error

    async def _post_state(
        self, payload: dict[str, Any], expected_session_id: str | None = None
    ) -> dict[str, Any]:
        if expected_session_id is not None:
            if self._session_validator is None or not await self._session_validator(
                expected_session_id
            ):
                raise WledSessionMismatchError("SESSION_MISMATCH")
        try:
            response = await self._require_client().post("/json/state", json=payload)
            return self._response_object(response)
        except httpx.HTTPError as error:
            raise WledUnavailableError("WLED 장치에 연결할 수 없습니다.") from error

    @staticmethod
    def _response_object(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 500: raise WledUnavailableError("WLED 장치를 현재 사용할 수 없습니다.")
        if not response.is_success: raise WledProtocolError("WLED가 올바르지 않은 응답을 반환했습니다.")
        try: payload = response.json()
        except ValueError as error: raise WledProtocolError("WLED 응답이 JSON이 아닙니다.") from error
        if not isinstance(payload, dict): raise WledProtocolError("WLED 응답 형식이 올바르지 않습니다.")
        return payload

    def _parse_capabilities(self, data: dict[str, Any]) -> WledCapabilities:
        info, effects, palettes = data.get("info"), data.get("effects"), data.get("palettes")
        if not isinstance(data.get("state"), dict) or not isinstance(info, dict) or not isinstance(effects, list) or not isinstance(palettes, list):
            raise WledProtocolError("WLED capabilities 응답 형식이 올바르지 않습니다.")
        name, version = info.get("name"), info.get("ver")
        if not isinstance(name, str) or not isinstance(version, str): raise WledProtocolError("WLED 장치 정보가 올바르지 않습니다.")
        valid_effects = tuple(WledCatalogItem(i, item.strip()) for i, item in enumerate(effects) if isinstance(item, str) and item.strip() and item.strip() not in {"RSVD", "-"})
        valid_palettes = tuple(WledCatalogItem(i, item.strip()) for i, item in enumerate(palettes) if isinstance(item, str) and item.strip())
        return WledCapabilities(name, version, valid_effects, valid_palettes, datetime.now(UTC))

    def _snapshot_from_state(self, state: dict[str, Any]) -> WledSnapshot:
        if not isinstance(state.get("on"), bool): raise WledProtocolError("WLED 전원 상태가 올바르지 않습니다.")
        brightness = state.get("bri")
        if not isinstance(brightness, int) or not 0 <= brightness <= 255:
            raise WledProtocolError("WLED 밝기 상태가 올바르지 않습니다.")
        segments = self._valid_segments(state)
        if not state["on"]: return WledSnapshot(WledStatus.ONLINE, False, brightness, WledMode.OFF, None, None, None, None, None, None, datetime.now(UTC), None)
        values = [self._segment_values(segment) for segment in segments]
        fx_values = {value[0] for value in values}
        colors = {value[4] for value in values}
        if fx_values == {0} and len(colors) == 1:
            fx, pal, sx, ix, color = values[0]
            return WledSnapshot(WledStatus.ONLINE, True, brightness, WledMode.SOLID, color, 0, "Solid", pal, sx, ix, datetime.now(UTC), None)
        same_effect = all(value[:4] == values[0][:4] for value in values[1:]) and values[0][0] != 0
        if not same_effect:
            return WledSnapshot(WledStatus.ONLINE, True, brightness, WledMode.MIXED, None, None, None, None, None, None, datetime.now(UTC), None)
        fx, pal, sx, ix, color = values[0]
        color = color if len(colors) == 1 else None
        effect_name = next((item.name for item in (self._capabilities.effects if self._capabilities else ()) if item.id == fx), None)
        return WledSnapshot(WledStatus.ONLINE, True, brightness, WledMode.EFFECT, color, fx, effect_name, pal, sx, ix, datetime.now(UTC), None)

    @staticmethod
    def _valid_segments(state: dict[str, Any]) -> list[dict[str, Any]]:
        segments = state.get("seg")
        if not isinstance(segments, list): raise WledProtocolError("WLED segment 목록이 올바르지 않습니다.")
        valid = [item for item in segments if isinstance(item, dict) and isinstance(item.get("id"), int) and item["id"] >= 0 and ((isinstance(item.get("start"), int) and isinstance(item.get("stop"), int) and item["stop"] > item["start"]) or (isinstance(item.get("len"), int) and item["len"] > 0))]
        if not valid: raise WledProtocolError("WLED에 유효한 segment가 없습니다.")
        return valid

    def _segment_values(self, segment: dict[str, Any]) -> tuple[int, int, int | None, int | None, str | None]:
        fx, pal = segment.get("fx"), segment.get("pal")
        if not isinstance(fx, int) or not isinstance(pal, int): raise WledProtocolError("WLED segment 설정이 올바르지 않습니다.")
        sx, ix = segment.get("sx"), segment.get("ix")
        if sx is not None and not isinstance(sx, int) or ix is not None and not isinstance(ix, int): raise WledProtocolError("WLED effect 설정이 올바르지 않습니다.")
        return fx, pal, sx, ix, self._segment_color(segment)

    def _verify_response(self, response: dict[str, Any], expected: dict[int, dict[str, Any]]) -> None:
        if not isinstance(response.get("on"), bool) or not response["on"]: raise WledProtocolError("WLED가 조명을 켜지 못했습니다.")
        found = {segment["id"]: segment for segment in self._valid_segments(response)}
        for item_id, wanted in expected.items():
            segment = found.get(item_id)
            if segment is None: raise WledProtocolError("WLED 응답에 적용한 segment가 없습니다.")
            actual = self._segment_values(segment)
            if actual[0] != wanted["fx"] or actual[1] != wanted["pal"] or ("sx" in wanted and actual[2] != wanted["sx"]) or ("ix" in wanted and actual[3] != wanted["ix"]) or ("col" in wanted and actual[4] != self._hex(wanted["col"])):
                raise WledProtocolError("WLED가 요청한 설정을 확인하지 못했습니다.")

    @staticmethod
    def _segment_color(segment: dict[str, Any]) -> str | None:
        colors = segment.get("col")
        if not isinstance(colors, list) or not colors or not isinstance(colors[0], list): return None
        return WledClient._hex(colors[0])

    @staticmethod
    def _hex(rgb: Any) -> str | None:
        if not isinstance(rgb, list) or len(rgb) < 3 or any(not isinstance(value, int) or not 0 <= value <= 255 for value in rgb[:3]): return None
        return "".join(f"{value:02X}" for value in rgb[:3])
    @staticmethod
    def _rgb(color: str) -> list[int]: return [int(color[index:index + 2], 16) for index in range(0, 6, 2)]
    @staticmethod
    def _normalize_color(color: str) -> str:
        normalized = color.strip().upper()
        if not re.fullmatch(r"[0-9A-F]{6}", normalized): raise WledProtocolError("색상 형식이 올바르지 않습니다.")
        return normalized

    def _record_failure(self, error: WledError) -> None:
        previous = self._snapshot
        self._snapshot = WledSnapshot(WledStatus.ERROR, previous.on, previous.brightness, previous.mode, previous.color, previous.effect_id, previous.effect_name, previous.palette_id, previous.speed, previous.intensity, previous.observed_at, str(error))
        LOGGER.warning("WLED request failed", extra={"component": "wled", "event": "wled_request_failed", "error_code": type(error).__name__})
    @staticmethod
    def _log_applied(mode: str, **extra: Any) -> None:
        LOGGER.info("WLED command applied", extra={"component": "wled", "event": "wled_command_applied", "mode": mode, **extra})
