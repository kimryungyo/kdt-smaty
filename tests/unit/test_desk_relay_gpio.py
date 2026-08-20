"""GPIO 릴레이가 RelayClient와 같은 계약을 지키는지 확인한다.

ESP32 relay 보드를 걷어낸 뒤 DeskController는 이 구현을 그대로 쓴다. 특히
hold 만료 자동 정지와 방향 전환 인터록은 ESP32의 hardware timer가 하던 안전
동작을 대신하는 것이라 반드시 지켜져야 한다.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from smart_desk.modules.desk.controller import SUPPORTED_RELAY_FIRMWARES
from smart_desk.modules.desk.models import Direction, RelayState


UP_PIN, DOWN_PIN = 17, 27


@pytest.fixture
def writes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    """lgpio를 대체해 핀 쓰기 순서를 기록한다."""

    log: list[tuple[int, int]] = []
    stub = types.ModuleType("lgpio")
    stub.gpiochip_open = lambda chip: 1  # type: ignore[attr-defined]
    stub.gpio_claim_output = lambda h, pin, level: log.append((pin, level))  # type: ignore[attr-defined]
    stub.gpio_write = lambda h, pin, level: log.append((pin, level))  # type: ignore[attr-defined]
    stub.gpiochip_close = lambda h: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lgpio", stub)
    return log


def _client():
    from smart_desk.modules.desk.relay_gpio import GpioRelayClient

    return GpioRelayClient(UP_PIN, DOWN_PIN)


@pytest.mark.asyncio
async def test_initial_state_is_stop_and_pins_low(writes) -> None:
    relay = _client()
    # 열자마자 두 선을 LOW로 확정해 부팅 잔여 상태를 지운다.
    assert writes == [(UP_PIN, 0), (DOWN_PIN, 0)]

    snapshot = relay.get_snapshot()
    assert snapshot.state is RelayState.STOP
    assert snapshot.code == "ready"
    # DeskController의 펌웨어 화이트리스트를 통과해야 한다.
    assert snapshot.firmware in SUPPORTED_RELAY_FIRMWARES
    await relay.close()


@pytest.mark.asyncio
async def test_pulse_drives_only_requested_direction(writes) -> None:
    relay = _client()
    writes.clear()

    await relay.pulse(Direction.UP, 200)
    assert (UP_PIN, 1) in writes
    assert (DOWN_PIN, 1) not in writes
    assert relay.get_snapshot().state is RelayState.UP
    await relay.close()


@pytest.mark.asyncio
async def test_hold_expires_and_stops_without_further_commands(writes) -> None:
    """ESP32 timer ISR을 대신하는 deadline이 반드시 릴레이를 끈다."""

    relay = _client()
    await relay.pulse(Direction.UP, 100)
    assert relay.get_snapshot().state is RelayState.UP

    await asyncio.sleep(0.25)
    assert relay.get_snapshot().state is RelayState.STOP
    assert writes[-2:] == [(UP_PIN, 0), (DOWN_PIN, 0)]
    await relay.close()


@pytest.mark.asyncio
async def test_direction_change_never_energizes_both(writes) -> None:
    """두 릴레이가 동시에 붙으면 안 된다(break-before-make)."""

    relay = _client()
    await relay.pulse(Direction.UP, 300)
    writes.clear()

    await relay.pulse(Direction.DOWN, 300)
    energized = [entry for entry in writes if entry[1] == 1]
    assert energized == [(DOWN_PIN, 1)]
    # DOWN을 올리기 전에 UP을 반드시 내린다.
    assert writes.index((UP_PIN, 0)) < writes.index((DOWN_PIN, 1))
    await relay.close()


@pytest.mark.asyncio
async def test_send_stop_is_immediate(writes) -> None:
    relay = _client()
    await relay.pulse(Direction.UP, 500)
    writes.clear()

    await relay.send_stop()
    assert (UP_PIN, 0) in writes and (DOWN_PIN, 0) in writes
    assert relay.get_snapshot().state is RelayState.STOP

    # 정지 뒤에는 만료 task가 상태를 되돌리지 않아야 한다.
    await asyncio.sleep(0.2)
    assert relay.get_snapshot().state is RelayState.STOP
    await relay.close()


@pytest.mark.asyncio
async def test_snapshot_stays_fresh_for_staleness_check(writes) -> None:
    """GPIO에는 heartbeat wire가 없으므로 조회 시점을 received_at으로 준다."""

    relay = _client()
    first = relay.get_snapshot().received_at
    await asyncio.sleep(0.05)
    second = relay.get_snapshot().received_at
    assert first is not None and second is not None
    assert second > first
    await relay.close()


@pytest.mark.asyncio
async def test_rejects_out_of_contract_hold(writes) -> None:
    relay = _client()
    with pytest.raises(ValueError):
        await relay.pulse(Direction.UP, 10)
    with pytest.raises(ValueError):
        await relay.pulse(Direction.UP, 900)
    with pytest.raises(TypeError):
        await relay.pulse(Direction.UP, True)
    await relay.close()


@pytest.mark.asyncio
async def test_close_turns_relay_off(writes) -> None:
    relay = _client()
    await relay.pulse(Direction.UP, 500)
    writes.clear()

    await relay.close()
    assert (UP_PIN, 0) in writes and (DOWN_PIN, 0) in writes
