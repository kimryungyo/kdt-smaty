"""DeskController의 목표·HOLD·STOP과 fail-closed 계약 테스트."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from smart_desk.config.settings import DeskSettings
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.desk.controller import (
    SUPPORTED_RELAY_FIRMWARES,
    DeskCommandRejectedError,
    DeskController,
)
from smart_desk.modules.desk.models import (
    DeskState,
    Direction,
    HeightSnapshot,
    HeightStatus,
    RelayEvent,
    RelaySnapshot,
    RelayState,
)
from smart_desk.modules.mqtt.client import MqttUnavailableError


FIRMWARE = "smartdesk-fin-relay-1.0.0"


class FakeHeightMonitor:
    """테스트가 직접 교체하는 높이 snapshot 제공자."""

    def __init__(self, height_cm: float | None = 80.0) -> None:
        self.reset_active = False
        self.set_height(height_cm)

    def set_height(
        self,
        height_cm: float | None,
        *,
        status: HeightStatus = HeightStatus.ONLINE,
    ) -> None:
        self.snapshot = HeightSnapshot(
            height_cm=height_cm,
            observed_at=datetime.now(UTC) if height_cm is not None else None,
            status=status,
        )

    def get_snapshot(self) -> HeightSnapshot:
        return self.snapshot

    def panel_reset_active(self) -> bool:
        return self.reset_active


class FakeRelayClient:
    """명령 기록과 live firmware 상태를 함께 제공하는 relay fake."""

    def __init__(self, *, firmware: str = FIRMWARE) -> None:
        self.calls: list[tuple[str, object]] = []
        self.error: BaseException | None = None
        self._received_at = datetime.now(UTC)
        self.snapshot = RelaySnapshot(
            event=RelayEvent.ONLINE,
            state=RelayState.STOP,
            firmware=firmware,
            code="ready",
            detail="ready",
            received_at=self._received_at,
            last_error=None,
        )

    async def pulse(self, direction: Direction, hold_ms: int) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(("pulse", (direction, hold_ms)))
        self._advance_status(
            event=RelayEvent.MOVING,
            state=RelayState(direction.value),
            code="command_started",
        )

    async def wake(self, direction: Direction, basis_height_cm: float) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(("wake", (direction, basis_height_cm)))

    async def send_stop(self) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(("stop", None))
        self._advance_status(
            event=RelayEvent.STOPPED,
            state=RelayState.STOP,
            code="command",
        )

    def get_snapshot(self) -> RelaySnapshot:
        return self.snapshot

    def _advance_status(
        self,
        *,
        event: RelayEvent,
        state: RelayState,
        code: str,
    ) -> None:
        self._received_at += timedelta(microseconds=1)
        self.snapshot = RelaySnapshot(
            event=event,
            state=state,
            firmware=self.snapshot.firmware,
            code=code,
            detail=code,
            received_at=self._received_at,
            last_error=None,
        )


class StartupHeartbeatRelayClient(FakeRelayClient):
    """시작 직후 relay snapshot이 비었다가 heartbeat가 늦게 오는 fake."""

    def __init__(self) -> None:
        super().__init__(firmware="")
        self.snapshot = RelaySnapshot(
            event=None,
            state=None,
            firmware=None,
            code=None,
            detail=None,
            received_at=None,
            last_error=None,
        )

    async def send_stop(self) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(("stop", None))
        self._advance_status(
            event=RelayEvent.STOPPED,
            state=RelayState.STOP,
            code="command",
        )

    def publish_live_stop(self, *, code: str = "height_waiting") -> None:
        self._received_at += timedelta(microseconds=1)
        self.snapshot = RelaySnapshot(
            event=RelayEvent.HEARTBEAT,
            state=RelayState.STOP,
            firmware=FIRMWARE,
            code=code,
            detail=code,
            received_at=self._received_at,
            last_error=None,
        )


class BlockingRelayClient(FakeRelayClient):
    """pulse 도중 STOP 경쟁 순서를 제어하는 relay fake."""

    def __init__(self) -> None:
        super().__init__()
        self.pulse_started = asyncio.Event()
        self.release_pulse = asyncio.Event()

    async def pulse(self, direction: Direction, hold_ms: int) -> None:
        self.pulse_started.set()
        await self.release_pulse.wait()
        await super().pulse(direction, hold_ms)


class BlockingStopRelayClient(FakeRelayClient):
    """전환 STOP 도중 뒤따르는 사용자 STOP 경합을 제어하는 relay fake."""

    def __init__(self) -> None:
        super().__init__()
        self.block_stops = False
        self.stop_started = asyncio.Event()
        self.release_stop = asyncio.Event()

    async def send_stop(self) -> None:
        if self.block_stops:
            self.stop_started.set()
            await self.release_stop.wait()
            self.block_stops = False
        await super().send_stop()


class DelayedStopRelayClient(FakeRelayClient):
    """STOP publish와 firmware STOP status 도착을 분리하는 relay fake."""

    def __init__(self) -> None:
        super().__init__()
        self.delay_stops = False
        self.stop_sent = asyncio.Event()

    async def send_stop(self) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(("stop", None))
        self.stop_sent.set()
        if not self.delay_stops:
            self.confirm_stop()

    def confirm_stop(self) -> None:
        self._advance_status(
            event=RelayEvent.STOPPED,
            state=RelayState.STOP,
            code="command",
        )


def control_settings() -> DeskSettings:
    return DeskSettings(
        pulse_refresh_interval_seconds=0.02,
        control_poll_interval_seconds=0.005,
        manual_watchdog_seconds=0.05,
        target_timeout_seconds=1,
        fine_settle_seconds=0.01,
        relay_ack_timeout_seconds=0.05,
        relay_stale_after_seconds=0.5,
    )


async def wait_until(predicate, *, timeout: float = 0.5) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


def make_controller(
    height: FakeHeightMonitor,
    relay: FakeRelayClient,
    task_manager: TaskManager,
    *,
    settings: DeskSettings | None = None,
) -> DeskController:
    return DeskController(
        height,  # type: ignore[arg-type]
        relay,  # type: ignore[arg-type]
        settings or control_settings(),
        task_manager,
    )


async def test_start_and_stop_send_only_safe_stop_commands() -> None:
    height = FakeHeightMonitor()
    relay = FakeRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)

    await controller.start()
    assert controller.get_snapshot().state is DeskState.IDLE
    assert relay.calls == [("stop", None)]

    await controller.stop()
    assert controller.get_snapshot().state is DeskState.STOPPED
    assert relay.calls[-1] == ("stop", None)
    await tasks.shutdown()


async def test_target_refreshes_same_direction_and_stops_at_goal() -> None:
    height = FakeHeightMonitor(80.0)
    relay = FakeRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()

    await controller.set_target(90.0)
    await wait_until(
        lambda: len([call for call in relay.calls if call[0] == "pulse"]) >= 2
    )
    pulses = [call for call in relay.calls if call[0] == "pulse"]
    assert all(call[1] == (Direction.UP, 500) for call in pulses)
    assert not any(
        call[0] == "stop" for call in relay.calls[1:-1]
    )

    height.set_height(90.0)
    await asyncio.sleep(control_settings().control_poll_interval_seconds * 2)
    assert controller.get_snapshot().state is DeskState.MOVING
    height.set_height(90.0)
    await wait_until(lambda: controller.get_snapshot().state is DeskState.STOPPED)
    assert relay.calls[-1] == ("stop", None)
    assert controller.get_snapshot().target_height_cm is None

    await controller.stop()
    await tasks.shutdown()


async def test_target_requires_two_fresh_in_tolerance_height_frames() -> None:
    height = FakeHeightMonitor(80.0)
    relay = FakeRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()

    await controller.set_target(90.0)
    await wait_until(lambda: any(call[0] == "pulse" for call in relay.calls))

    height.set_height(90.0)
    await asyncio.sleep(control_settings().control_poll_interval_seconds * 2)
    assert controller.get_snapshot().state is DeskState.MOVING
    assert controller.get_snapshot().detail == "목표 높이를 한 번 더 확인하고 있습니다."

    height.set_height(80.0)
    await asyncio.sleep(control_settings().control_poll_interval_seconds * 2)
    assert controller.get_snapshot().state is DeskState.MOVING

    height.set_height(90.0)
    await asyncio.sleep(control_settings().control_poll_interval_seconds * 2)
    assert controller.get_snapshot().state is DeskState.MOVING
    height.set_height(90.0)
    await wait_until(lambda: controller.get_snapshot().state is DeskState.STOPPED)

    await controller.stop()
    await tasks.shutdown()


async def test_near_target_uses_single_fine_pulse_after_settling() -> None:
    height = FakeHeightMonitor(80.0)
    relay = FakeRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    await controller.set_target(81.2)

    await wait_until(
        lambda: any(
            call == ("pulse", (Direction.UP, 350)) for call in relay.calls
        )
    )
    fine_pulses = [
        call for call in relay.calls if call == ("pulse", (Direction.UP, 350))
    ]
    assert len(fine_pulses) == 1

    relay._advance_status(  # noqa: SLF001 - firmware timeout 재현
        event=RelayEvent.STOPPED,
        state=RelayState.STOP,
        code="timeout",
    )
    height.set_height(81.0)
    await asyncio.sleep(control_settings().control_poll_interval_seconds * 2)
    assert controller.get_snapshot().state is DeskState.MOVING
    height.set_height(81.0)
    await wait_until(lambda: controller.get_snapshot().state is DeskState.STOPPED)
    await controller.stop()
    await tasks.shutdown()


async def test_fresh_height_and_approved_firmware_are_required() -> None:
    height = FakeHeightMonitor(None,)
    relay = FakeRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()

    with pytest.raises(DeskCommandRejectedError, match="현재 높이"):
        await controller.set_target(90.0)
    assert not any(call[0] == "pulse" for call in relay.calls)
    await controller.stop()
    await tasks.shutdown()

    height = FakeHeightMonitor(80.0)
    relay = FakeRelayClient(firmware="smartdesk-relay-1.0.5")
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    with pytest.raises(DeskCommandRejectedError, match="승인되지 않은"):
        await controller.hold_up()
    await controller.stop()
    await tasks.shutdown()


@pytest.mark.parametrize(
    ("height_cm", "method"),
    [(115.0, "up"), (75.0, "down")],
)
async def test_manual_boundary_blocks_outward_direction(
    height_cm: float,
    method: str,
) -> None:
    height = FakeHeightMonitor(height_cm)
    relay = FakeRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()

    with pytest.raises(DeskCommandRejectedError):
        if method == "up":
            await controller.hold_up()
        else:
            await controller.hold_down()
    assert not any(call[0] == "pulse" for call in relay.calls)
    await controller.stop()
    await tasks.shutdown()


async def test_manual_watchdog_stops_after_hold_input_expires() -> None:
    height = FakeHeightMonitor(80.0)
    relay = FakeRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    await controller.hold_down()
    await wait_until(lambda: any(call[0] == "pulse" for call in relay.calls))
    await wait_until(lambda: controller.get_snapshot().state is DeskState.STOPPED)
    assert relay.calls[-1] == ("stop", None)
    await controller.stop()
    await tasks.shutdown()


async def test_user_stop_waits_for_fresh_stop_without_treating_old_down_as_external() -> None:
    height = FakeHeightMonitor(80.0)
    relay = DelayedStopRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    await controller.hold_down()
    await wait_until(lambda: relay.get_snapshot().state is RelayState.DOWN)

    relay.delay_stops = True
    relay.stop_sent.clear()
    stop_task = asyncio.create_task(controller.stop_motion("사용자 STOP"))
    await relay.stop_sent.wait()

    assert relay.get_snapshot().state is RelayState.DOWN
    assert not stop_task.done()
    assert controller.get_snapshot().state is DeskState.STOPPED
    assert controller.get_snapshot().last_error is None

    relay.confirm_stop()
    await stop_task
    assert controller.get_snapshot().state is DeskState.STOPPED
    assert controller.get_snapshot().last_error is None

    relay.delay_stops = False
    await controller.stop()
    await tasks.shutdown()


async def test_user_stop_without_fresh_stop_enters_error() -> None:
    height = FakeHeightMonitor(80.0)
    relay = DelayedStopRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    await controller.hold_down()
    await wait_until(lambda: relay.get_snapshot().state is RelayState.DOWN)

    relay.delay_stops = True
    with pytest.raises(RuntimeError, match="최신 STOP 응답"):
        await controller.stop_motion("사용자 STOP")

    snapshot = controller.get_snapshot()
    assert snapshot.state is DeskState.ERROR
    assert "최신 STOP 응답" in (snapshot.last_error or "")
    stop_count = len([call for call in relay.calls if call[0] == "stop"])
    await controller._run_cycle()  # noqa: SLF001 - STOP baseline 재검사 방지 검증
    assert len([call for call in relay.calls if call[0] == "stop"]) == stop_count

    relay.delay_stops = False
    await controller.stop()
    await tasks.shutdown()


async def test_new_movement_after_fresh_stop_is_stopped_as_external() -> None:
    height = FakeHeightMonitor(80.0)
    relay = DelayedStopRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    await controller.hold_down()
    await wait_until(lambda: relay.get_snapshot().state is RelayState.DOWN)

    relay.delay_stops = True
    relay.stop_sent.clear()
    stop_task = asyncio.create_task(controller.stop_motion("사용자 STOP"))
    await relay.stop_sent.wait()
    relay.confirm_stop()
    await stop_task
    stop_count = len([call for call in relay.calls if call[0] == "stop"])

    relay.delay_stops = False
    relay._advance_status(  # noqa: SLF001 - STOP 뒤 외부 이동 재현
        event=RelayEvent.MOVING,
        state=RelayState.UP,
        code="external",
    )
    await wait_until(lambda: controller.get_snapshot().state is DeskState.ERROR)

    assert "예기치 않은 릴레이 이동 상태" in (
        controller.get_snapshot().last_error or ""
    )
    assert len([call for call in relay.calls if call[0] == "stop"]) == stop_count + 1

    await controller.stop()
    await tasks.shutdown()


async def test_manual_watchdog_waits_for_fresh_stop() -> None:
    height = FakeHeightMonitor(80.0)
    relay = DelayedStopRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()

    relay.delay_stops = True
    relay.stop_sent.clear()
    await controller.hold_down()
    await relay.stop_sent.wait()

    assert relay.get_snapshot().state is RelayState.DOWN
    assert controller.get_snapshot().last_error is None
    assert controller._stop_in_progress  # noqa: SLF001 - fresh STOP 대기 계약 검증

    relay.confirm_stop()
    await wait_until(lambda: not controller._stop_in_progress)  # noqa: SLF001
    assert controller.get_snapshot().state is DeskState.STOPPED
    assert controller.get_snapshot().last_error is None

    relay.delay_stops = False
    await controller.stop()
    await tasks.shutdown()


async def test_consecutive_user_stops_serialize_fresh_stop_confirmation() -> None:
    height = FakeHeightMonitor(80.0)
    relay = DelayedStopRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    await controller.hold_down()
    await wait_until(lambda: relay.get_snapshot().state is RelayState.DOWN)

    relay.delay_stops = True
    relay.stop_sent.clear()
    initial_stop_count = len([call for call in relay.calls if call[0] == "stop"])
    release_stop = asyncio.create_task(controller.stop_motion("pointer release"))
    await relay.stop_sent.wait()
    blur_stop = asyncio.create_task(controller.stop_motion("pointer blur"))
    await asyncio.sleep(0)
    assert len([call for call in relay.calls if call[0] == "stop"]) == initial_stop_count + 1

    relay.stop_sent.clear()
    relay.confirm_stop()
    await release_stop
    await relay.stop_sent.wait()
    assert len([call for call in relay.calls if call[0] == "stop"]) == initial_stop_count + 2

    relay.confirm_stop()
    await blur_stop
    assert controller.get_snapshot().state is DeskState.STOPPED
    assert controller.get_snapshot().last_error is None

    relay.delay_stops = False
    await controller.stop()
    await tasks.shutdown()


async def test_cancelled_user_stop_finishes_fresh_stop_confirmation() -> None:
    height = FakeHeightMonitor(80.0)
    relay = DelayedStopRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    await controller.hold_down()
    await wait_until(lambda: relay.get_snapshot().state is RelayState.DOWN)

    relay.delay_stops = True
    relay.stop_sent.clear()
    stop_task = asyncio.create_task(controller.stop_motion("취소되는 사용자 STOP"))
    await relay.stop_sent.wait()
    stop_task.cancel()
    await asyncio.sleep(0)

    assert controller._stop_in_progress  # noqa: SLF001 - 취소 중 안전 STOP 유지 검증
    relay.confirm_stop()
    result = await asyncio.gather(stop_task, return_exceptions=True)

    assert isinstance(result[0], asyncio.CancelledError)
    assert controller._stop_in_progress is False  # noqa: SLF001
    assert controller.get_snapshot().state is DeskState.STOPPED
    assert controller.get_snapshot().last_error is None

    relay.delay_stops = False
    await controller.stop()
    await tasks.shutdown()


async def test_shutdown_does_not_wait_for_live_stop_status() -> None:
    height = FakeHeightMonitor(80.0)
    relay = DelayedStopRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    await controller.hold_down()
    await wait_until(lambda: relay.get_snapshot().state is RelayState.DOWN)

    relay.delay_stops = True
    async with asyncio.timeout(control_settings().relay_ack_timeout_seconds * 2):
        await controller.stop()

    assert controller.get_snapshot().state is DeskState.STOPPED
    assert relay.get_snapshot().state is RelayState.DOWN
    await tasks.shutdown()


async def test_active_target_is_stopped_when_replacement_is_invalid() -> None:
    height = FakeHeightMonitor(80.0)
    relay = FakeRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    await controller.set_target(90.0)
    await wait_until(lambda: any(call[0] == "pulse" for call in relay.calls))

    with pytest.raises(ValueError):
        await controller.set_target(200.0)
    assert controller.get_snapshot().state is DeskState.STOPPED
    assert relay.calls[-1] == ("stop", None)
    await controller.stop()
    await tasks.shutdown()


async def test_pulse_publish_failure_attempts_stop_and_enters_error() -> None:
    height = FakeHeightMonitor(80.0)
    relay = FakeRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    relay.error = MqttUnavailableError("offline")
    await controller.set_target(90.0)

    await wait_until(lambda: controller.get_snapshot().state is DeskState.ERROR)
    assert "offline" in (controller.get_snapshot().last_error or "")
    relay.error = None
    await controller.stop()
    await tasks.shutdown()


async def test_stale_height_during_motion_stops_and_enters_error() -> None:
    height = FakeHeightMonitor(80.0)
    relay = FakeRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    await controller.set_target(90.0)
    await wait_until(lambda: any(call[0] == "pulse" for call in relay.calls))

    height.set_height(80.0, status=HeightStatus.STALE)
    await wait_until(lambda: controller.get_snapshot().state is DeskState.ERROR)

    assert "현재 높이" in (controller.get_snapshot().last_error or "")
    assert relay.calls[-1] == ("stop", None)
    await controller.stop()
    await tasks.shutdown()


async def test_external_runner_cancellation_sends_final_stop() -> None:
    height = FakeHeightMonitor(80.0)
    relay = FakeRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    await controller.set_target(90.0)
    await wait_until(lambda: any(call[0] == "pulse" for call in relay.calls))

    runner = controller._runner_task  # noqa: SLF001 - lifecycle 이탈 취소 재현
    assert runner is not None
    runner.cancel()
    await asyncio.gather(runner, return_exceptions=True)
    await wait_until(lambda: controller.get_snapshot().state is DeskState.ERROR)

    assert "lifecycle 밖에서 취소" in (controller.get_snapshot().last_error or "")
    assert relay.calls[-1] == ("stop", None)
    await controller.stop()
    await tasks.shutdown()


async def test_user_stop_wins_during_target_transition_stop() -> None:
    height = FakeHeightMonitor(80.0)
    relay = BlockingStopRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    await controller.set_target(90.0)
    await wait_until(lambda: any(call[0] == "pulse" for call in relay.calls))

    relay.block_stops = True
    replacement = asyncio.create_task(controller.set_target(78.0))
    await relay.stop_started.wait()
    final_stop = asyncio.create_task(controller.stop_motion("사용자 최종 STOP"))
    relay.release_stop.set()
    await replacement
    await final_stop

    assert controller.get_snapshot().state is DeskState.STOPPED
    assert controller.get_snapshot().target_height_cm is None
    assert relay.calls[-1] == ("stop", None)
    await controller.stop()
    await tasks.shutdown()


async def test_stop_waits_for_inflight_pulse_and_is_last_relay_call() -> None:
    height = FakeHeightMonitor(80.0)
    relay = BlockingRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    await controller.set_target(90.0)
    await relay.pulse_started.wait()

    stop_task = asyncio.create_task(controller.stop_motion("race test"))
    await asyncio.sleep(0)
    assert not stop_task.done()
    relay.release_pulse.set()
    await stop_task

    assert relay.calls[-2][0] == "pulse"
    assert relay.calls[-1] == ("stop", None)
    await controller.stop()
    await tasks.shutdown()


async def test_stale_manual_hold_checks_boundary_then_wakes_once_before_motion() -> None:
    height = FakeHeightMonitor(80.0)
    relay = FakeRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    height.set_height(74.0, status=HeightStatus.STALE)

    with pytest.raises(DeskCommandRejectedError, match="하한"):
        await controller.hold_down()
    assert [call for call in relay.calls if call[0] == "wake"] == []

    height.set_height(80.0, status=HeightStatus.STALE)
    await asyncio.gather(controller.hold_up(), controller.hold_up())
    assert controller.get_snapshot().state is DeskState.WAKING
    assert [call for call in relay.calls if call[0] == "wake"] == [
        ("wake", (Direction.UP, 80.0))
    ]
    assert not any(call[0] == "pulse" for call in relay.calls)

    relay._advance_status(event=RelayEvent.HEARTBEAT, state=RelayState.STOP, code="ready")  # noqa: SLF001
    height.set_height(80.1)
    await wait_until(lambda: any(call[0] == "pulse" for call in relay.calls))
    assert any(call == ("pulse", (Direction.UP, 500)) for call in relay.calls)

    await controller.stop()
    await tasks.shutdown()


async def test_stale_target_wakes_then_recalculates_with_new_height() -> None:
    height = FakeHeightMonitor(80.0)
    relay = FakeRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    height.set_height(80.0, status=HeightStatus.STALE)

    await controller.set_target(90.0)
    assert controller.get_snapshot().state is DeskState.WAKING
    assert relay.calls[-1] == ("wake", (Direction.UP, 80.0))
    assert not any(call[0] == "pulse" for call in relay.calls)

    relay._advance_status(event=RelayEvent.HEARTBEAT, state=RelayState.STOP, code="ready")  # noqa: SLF001
    height.set_height(90.0)
    await wait_until(lambda: controller.get_snapshot().state is DeskState.STOPPED)
    assert not any(call[0] == "pulse" for call in relay.calls)

    await controller.stop()
    await tasks.shutdown()


async def test_wake_waits_for_relay_ready_after_fresh_height() -> None:
    height = FakeHeightMonitor(80.0)
    relay = FakeRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    height.set_height(80.0, status=HeightStatus.STALE)

    await controller.set_target(90.0)
    relay._advance_status(event=RelayEvent.MOVING, state=RelayState.UP, code="wake_started")  # noqa: SLF001
    height.set_height(80.1)
    await asyncio.sleep(control_settings().control_poll_interval_seconds * 2)
    assert controller.get_snapshot().state is DeskState.WAKING
    assert not any(call[0] == "pulse" for call in relay.calls)

    relay._advance_status(event=RelayEvent.HEARTBEAT, state=RelayState.STOP, code="ready")  # noqa: SLF001
    await wait_until(lambda: any(call[0] == "pulse" for call in relay.calls))

    await controller.stop()
    await tasks.shutdown()


async def test_wake_extends_same_direction_when_fresh_height_is_ready_before_deadline() -> None:
    height = FakeHeightMonitor(80.0)
    relay = FakeRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    height.set_height(80.0, status=HeightStatus.STALE)

    await controller.set_target(90.0)
    relay._advance_status(event=RelayEvent.MOVING, state=RelayState.UP, code="wake_started")  # noqa: SLF001
    height.set_height(80.1)
    relay._advance_status(event=RelayEvent.ONLINE, state=RelayState.UP, code="ready")  # noqa: SLF001

    await wait_until(lambda: any(call[0] == "pulse" for call in relay.calls))
    assert controller.get_snapshot().state is DeskState.MOVING
    assert ("pulse", (Direction.UP, 500)) in relay.calls

    await controller.stop()
    await tasks.shutdown()


async def test_startup_wake_waits_for_stop_before_completing_sensor_check() -> None:
    height = FakeHeightMonitor(80.0)
    height.set_height(80.0, status=HeightStatus.SENSOR_SLEEPING)
    relay = FakeRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    await wait_until(lambda: any(call[0] == "wake" for call in relay.calls))

    relay._advance_status(event=RelayEvent.MOVING, state=RelayState.UP, code="wake_started")  # noqa: SLF001
    height.set_height(80.1)
    relay._advance_status(event=RelayEvent.ONLINE, state=RelayState.UP, code="ready")  # noqa: SLF001
    await asyncio.sleep(control_settings().control_poll_interval_seconds * 2)
    assert controller.get_snapshot().state is DeskState.WAKING

    relay._advance_status(event=RelayEvent.STOPPED, state=RelayState.STOP, code="ready")  # noqa: SLF001
    await wait_until(lambda: controller.get_snapshot().state is DeskState.IDLE)
    assert controller.get_snapshot().last_error is None

    await controller.stop()
    await tasks.shutdown()


async def test_stale_target_near_cache_requires_sensor_confirmation_without_wake() -> None:
    height = FakeHeightMonitor(80.0)
    relay = FakeRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    height.set_height(80.0, status=HeightStatus.STALE)

    await controller.set_target(80.1)

    assert controller.get_snapshot().state is DeskState.IDLE
    assert "센서 높이" in controller.get_snapshot().detail
    assert not any(call[0] in {"wake", "pulse"} for call in relay.calls)

    await controller.stop()
    await tasks.shutdown()


async def test_startup_uses_cached_height_for_one_wake_and_serial_error_skips_it() -> None:
    height = FakeHeightMonitor(80.0)
    height.set_height(80.0, status=HeightStatus.SENSOR_SLEEPING)
    relay = FakeRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)

    await controller.start()
    await wait_until(lambda: any(call[0] == "wake" for call in relay.calls))
    assert [call for call in relay.calls if call[0] == "wake"] == [
        ("wake", (Direction.UP, 80.0))
    ]
    assert controller.get_snapshot().state is DeskState.WAKING
    await controller.stop()
    await tasks.shutdown()

    height = FakeHeightMonitor(80.0)
    height.set_height(80.0, status=HeightStatus.ERROR)
    relay = FakeRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()
    assert not any(call[0] == "wake" for call in relay.calls)
    assert controller.get_snapshot().state is DeskState.IDLE
    await controller.stop()
    await tasks.shutdown()


async def test_startup_wake_waits_for_late_live_heartbeat_without_blocking_start() -> None:
    height = FakeHeightMonitor(80.0)
    height.set_height(80.0, status=HeightStatus.SENSOR_SLEEPING)
    relay = StartupHeartbeatRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)

    await asyncio.wait_for(controller.start(), timeout=0.1)

    assert controller.get_snapshot().state is DeskState.IDLE
    assert [call for call in relay.calls if call[0] == "wake"] == []

    relay.publish_live_stop()
    await wait_until(lambda: len([call for call in relay.calls if call[0] == "wake"]) == 1)

    assert [call for call in relay.calls if call[0] == "wake"] == [
        ("wake", (Direction.UP, 80.0))
    ]
    assert not any(call[0] == "pulse" for call in relay.calls)

    await controller.stop()
    await tasks.shutdown()


async def test_startup_wake_pending_times_out_without_pulse_and_keeps_controller_idle() -> None:
    height = FakeHeightMonitor(80.0)
    height.set_height(80.0, status=HeightStatus.SENSOR_SLEEPING)
    relay = StartupHeartbeatRelayClient()
    tasks = TaskManager()
    settings = control_settings().model_copy(update={"wake_timeout_seconds": 0.06})
    controller = make_controller(height, relay, tasks, settings=settings)

    await controller.start()
    await wait_until(
        lambda: controller.get_snapshot().last_error is not None,
        timeout=0.2,
    )

    snapshot = controller.get_snapshot()
    assert snapshot.state is DeskState.IDLE
    assert snapshot.detail == "높이 센서 확인이 필요합니다."
    assert snapshot.last_error is not None
    assert "릴레이 live 상태" in snapshot.last_error
    assert not any(call[0] == "wake" for call in relay.calls)

    await controller.stop()
    await tasks.shutdown()


async def test_panel_reset_holds_down_then_stops_on_normal_height() -> None:
    height = FakeHeightMonitor(110.0)
    height.reset_active = True
    relay, tasks = FakeRelayClient(), TaskManager()
    controller = make_controller(height, relay, tasks)
    await controller.start()

    await wait_until(lambda: any(call[0] == "wake" for call in relay.calls))
    snapshot = controller.get_snapshot()
    assert snapshot.state is DeskState.WAKING
    assert snapshot.direction is Direction.DOWN
    assert [call for call in relay.calls if call[0] == "wake"][0] == ("wake", (Direction.DOWN, 75.1))

    height.reset_active = False
    height.set_height(73.0)
    await wait_until(lambda: controller.get_snapshot().state is DeskState.STOPPED)
    assert any(call[0] == "stop" for call in relay.calls)
    await controller.stop()
    await tasks.shutdown()


async def test_user_target_cancels_startup_wake_pending_before_late_heartbeat() -> None:
    height = FakeHeightMonitor(80.0)
    height.set_height(80.0, status=HeightStatus.SENSOR_SLEEPING)
    relay = StartupHeartbeatRelayClient()
    tasks = TaskManager()
    controller = make_controller(height, relay, tasks)

    await controller.start()
    with pytest.raises(DeskCommandRejectedError):
        await controller.set_target(90.0)

    relay.publish_live_stop()
    await asyncio.sleep(control_settings().control_poll_interval_seconds * 3)

    assert not any(call[0] == "wake" for call in relay.calls)
    await controller.stop()
    await tasks.shutdown()


def test_supported_firmwares_cover_the_shipped_firmware_version() -> None:
    """펌웨어가 보고하는 이름이 허용 목록에 없으면 모든 이동이 거부된다.

    통합 펌웨어로 바꾸면서 FIRMWARE_VERSION만 고치고 이 목록을 빠뜨려,
    대시보드 제어가 "승인되지 않은 릴레이 펌웨어"로 전부 막혔던 적이 있다.
    실제 헤더에서 값을 읽어 두 곳이 어긋나면 여기서 먼저 깨지게 한다.
    """

    import re
    from pathlib import Path

    header = Path("firmware/desk-controller/include/config.h")
    if not header.exists():  # 펌웨어를 함께 두지 않는 환경에서는 건너뛴다.
        return
    text = header.read_text(encoding="utf-8")
    # SmartDeskConfig(relay)의 FIRMWARE_VERSION이 이 검사의 대상이다.
    match = re.search(
        r'constexpr char FIRMWARE_VERSION\[\]\s*=\s*"([^"]+)"', text
    )
    assert match is not None, "config.h에서 FIRMWARE_VERSION을 찾지 못했습니다."
    assert match.group(1) in SUPPORTED_RELAY_FIRMWARES
