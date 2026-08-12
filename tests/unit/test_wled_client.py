from __future__ import annotations

import httpx
import pytest
from pydantic import TypeAdapter, ValidationError

from smart_desk.config.settings import WledSettings
from smart_desk.modules.wled.client import WledClient, WledProtocolError
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
