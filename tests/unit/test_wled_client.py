from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from smart_desk.api.routes import wled as wled_route
from smart_desk.config.settings import WledSettings
from smart_desk.modules.wled.client import (
    WledClient,
    WledProtocolError,
    WledSessionMismatchError,
)
from smart_desk.modules.wled.models import ControlRequest, WledMode


def _state(*, on: bool = True, bri: int = 128, fx: int = 0, color: list[int] | None = None) -> dict:
    return {"on": on, "bri": bri, "seg": [{"id": 0, "start": 0, "stop": 10, "fx": fx, "pal": 0, "sx": 128, "ix": 128, "col": [color or [255, 48, 0]]}, {"id": 2, "start": 10, "stop": 20, "fx": fx, "pal": 0, "sx": 128, "ix": 128, "col": [color or [255, 48, 0]]}]}


@pytest.mark.asyncio
async def test_solid_applies_to_each_valid_segment_and_verifies_response() -> None:
    calls: list[dict] = []
    state = _state()
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET": return httpx.Response(200, json=state)
        payload = __import__("json").loads(request.content)
        calls.append(payload)
        return httpx.Response(200, json={"on": True, "bri": state["bri"], "seg": [{**segment, **next(item for item in payload["seg"] if item["id"] == segment["id"])} for segment in state["seg"]]})
    client = WledClient(WledSettings())
    await client.start()
    await client._client.aclose()  # noqa: SLF001
    client._client = httpx.AsyncClient(base_url="http://wled.test", transport=httpx.MockTransport(handler))  # noqa: SLF001
    result = await client.set_solid("ff3000")
    assert result.mode is WledMode.SOLID
    assert calls == [{"seg": [{"id": 0, "fx": 0, "pal": 0, "col": [[255, 48, 0]]}, {"id": 2, "fx": 0, "pal": 0, "col": [[255, 48, 0]]}], "v": True}]
    await client.stop()


@pytest.mark.asyncio
async def test_brightness_only_updates_wled_master_brightness() -> None:
    calls: list[dict] = []
    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        calls.append(payload)
        return httpx.Response(200, json=_state(bri=payload["bri"]))
    client = WledClient(WledSettings())
    await client.start()
    await client._client.aclose()  # noqa: SLF001
    client._client = httpx.AsyncClient(base_url="http://wled.test", transport=httpx.MockTransport(handler))  # noqa: SLF001
    result = await client.set_brightness(42)
    assert result.brightness == 42
    assert calls == [{"bri": 42, "v": True}]
    await client.stop()


@pytest.mark.asyncio
async def test_turn_on_updates_only_master_power_and_verifies_response() -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        calls.append(payload)
        return httpx.Response(200, json=_state(on=True, bri=77))

    client = WledClient(WledSettings())
    await client.start()
    await client._client.aclose()  # noqa: SLF001
    client._client = httpx.AsyncClient(  # noqa: SLF001
        base_url="http://wled.test", transport=httpx.MockTransport(handler)
    )

    result = await client.turn_on()

    assert result.on is True
    assert result.brightness == 77
    assert calls == [{"on": True, "v": True}]
    await client.stop()


@pytest.mark.parametrize("brightness", [-1, 256, True])
def test_brightness_request_rejects_out_of_range_and_boolean_values(brightness: object) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ControlRequest).validate_python({"action": "BRIGHTNESS", "brightness": brightness})


@pytest.mark.asyncio
async def test_mismatched_post_response_is_not_reported_as_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_state(color=[0, 0, 0]))
    client = WledClient(WledSettings())
    await client.start()
    await client._client.aclose()  # noqa: SLF001
    client._client = httpx.AsyncClient(base_url="http://wled.test", transport=httpx.MockTransport(handler))  # noqa: SLF001
    with pytest.raises(WledProtocolError): await client.set_solid("FF3000")
    assert client.get_snapshot().status == "ERROR"
    await client.stop()


@pytest.mark.asyncio
async def test_expected_session_is_revalidated_before_every_wled_post() -> None:
    calls: list[dict] = []
    valid = iter([True, False])

    async def validate(_session_id: str) -> bool:
        return next(valid)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_state(on=False))
        calls.append(__import__("json").loads(request.content))
        return httpx.Response(200, json=_state(on=True))

    client = WledClient(WledSettings(), session_validator=validate)
    await client.start()
    await client._client.aclose()  # noqa: SLF001
    client._client = httpx.AsyncClient(  # noqa: SLF001
        base_url="http://wled.test", transport=httpx.MockTransport(handler)
    )

    with pytest.raises(WledSessionMismatchError):
        await client.set_solid("FF3000", expected_session_id="session-a")

    assert calls == [{"on": True, "v": True}]
    await client.stop()


@pytest.mark.asyncio
async def test_expected_session_without_validator_fails_closed() -> None:
    client = WledClient(WledSettings())
    await client.start()
    with pytest.raises(WledSessionMismatchError):
        await client.set_brightness(10, expected_session_id="session-a")
    await client.stop()


def test_wled_route_rejects_stale_expected_session_before_client_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CurrentUser:
        async def snapshot(self) -> object:
            return type("Snapshot", (), {"session_id": "session-current"})()

    class Client:
        async def set_brightness(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("stale route must not call the WLED client")

    app = FastAPI()
    app.include_router(wled_route.router)
    monkeypatch.setattr(wled_route, "get_wled", lambda: Client())
    monkeypatch.setattr(
        wled_route,
        "get_container",
        lambda: type("Container", (), {"current_user": CurrentUser()})(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/wled/control",
            json={
                "action": "BRIGHTNESS",
                "brightness": 50,
                "expectedSessionId": "session-stale",
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SESSION_MISMATCH"
