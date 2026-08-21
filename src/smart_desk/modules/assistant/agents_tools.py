"""Context-bound local function tools for the single Smart Desk Agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Annotated

from agents import RunContextWrapper, function_tool
from pydantic import Field

from smart_desk.modules.assistant.context import CurrentUserSessionManager, TurnContext
from smart_desk.modules.assistant.memory import ProfileMemoryService
from smart_desk.modules.assistant.turns import AssistantTurnStore, TurnPhase
from smart_desk.modules.automation.models import ControlMode
from smart_desk.modules.automation.service import AutomationService
from smart_desk.modules.desk.models import Direction
from smart_desk.modules.wled.client import WledClient


@dataclass(slots=True)
class SmartDeskAgentContext:
    """Per-final-transcript state; never retained across a voice run."""

    turn_context: TurnContext
    sessions: CurrentUserSessionManager
    memory: ProfileMemoryService
    turns: AssistantTurnStore
    turn_id: str
    turn_sequence: int
    automation: AutomationService
    wled: WledClient | None = None
    # 틸팅과 작업 모드도 말로 다룬다. 하드웨어나 저장소가 없으면 None이다.
    tilt: Any | None = None
    activity_modes: Any | None = None
    # 이 책상이 실제로 받는 틸트 단계 범위. 도구가 값을 거를 때 쓴다.
    tilt_level_range: tuple[int, int] = (0, 3)
    # 얼굴 인식이 끊긴 동안 개인화 명령이 참고할 직전 사용자. 없으면 None이다.
    recent_user: Any | None = None
    # Dashboard snapshot과 작업 시간 집계는 짧은 시연용 읽기 도구가 재사용한다.
    dashboard: Any | None = None
    mode_usage: Any | None = None
    followup_requested: bool = False
    assistant_response: str = ""

    async def personalization_profile_id(self) -> str | None:
        """개인화 명령이 쓸 profile_id. 살아 있는 세션이 언제나 우선한다."""
        if self.turn_context.profile_id is not None:
            return self.turn_context.profile_id
        if self.recent_user is None:
            return None
        return await self.recent_user.profile_id()

    async def valid_mutation(self) -> bool:
        return self.turn_context.session_id is not None and await self.sessions.is_valid(self.turn_context)

    async def tool_started(self) -> None:
        """Record the next same-turn tool phase, if this turn is still live."""
        self.turn_sequence += 1
        await self.turns.update(self.turn_id, sequence=self.turn_sequence, phase=TurnPhase.TOOL)

    async def processing_started(self) -> None:
        """Record the final-transcript processing transition for this turn."""
        from smart_desk.modules.assistant.turns import TurnPhase

        self.turn_sequence += 1
        await self.turns.update(
            self.turn_id, sequence=self.turn_sequence, phase=TurnPhase.PROCESSING
        )

    def append_assistant_response(self, text: str) -> None:
        """Keep only the streamed assistant answer for this turn's final screen."""
        self.assistant_response += text

    async def finish(self, status: Any, *, error_code: str | None = None) -> None:
        """Record a terminal transition; a cleared/stale turn is a harmless no-op."""
        from smart_desk.modules.assistant.turns import TurnPhase

        self.turn_sequence += 1
        summary: str | None = None
        detail: str | None = None
        if status.value == "SUCCEEDED":
            response = self.assistant_response.strip()
            summary = response[:200]
            detail = response if len(response) > len(summary) else None
        await self.turns.update(
            self.turn_id,
            sequence=self.turn_sequence,
            phase=TurnPhase.FINAL,
            status=status,
            summary=summary,
            detail=detail,
            error_code=error_code,
        )


def _ok(**result: object) -> dict[str, object]:
    return {"ok": True, "result": result}


def _error(code: str) -> dict[str, object]:
    return {"ok": False, "error": {"code": code}}


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def build_smart_desk_tools() -> list[Any]:
    """Build the local SDK function tools for a per-turn context."""

    async def guarded(ctx: RunContextWrapper[SmartDeskAgentContext]) -> SmartDeskAgentContext | None:
        # 기기 제어는 얼굴 인식 세션과 무관하게 항상 받는다. 로그인한 사용자가
        # 없거나 인식이 흔들리는 동안에도 사용자가 말로 책상을 다룰 수 있어야 한다.
        context = ctx.context
        await context.tool_started()
        return context

    @function_tool
    async def stop_desk(ctx: RunContextWrapper[SmartDeskAgentContext]) -> dict[str, object]:
        """Immediately stop desk motion. This safety command is always allowed."""
        await ctx.context.tool_started()
        try:
            await ctx.context.automation.stop_motion()
            return _ok(action="stopped")
        except Exception:
            return _error("desk_command_failed")

    @function_tool
    async def get_desk_status(
        ctx: RunContextWrapper[SmartDeskAgentContext],
    ) -> dict[str, object]:
        """Read the current desk height, motion, target, control mode, and activity mode."""
        context = ctx.context
        await context.tool_started()
        if context.dashboard is None:
            return _error("desk_status_unavailable")
        try:
            desk = context.dashboard.get_status()
            automation = context.automation.get_snapshot()
        except Exception:
            return _error("desk_status_unavailable")
        activity_mode = automation.activity_mode
        return _ok(
            height_cm=desk.height.height_cm,
            height_status=_enum_value(desk.height.status),
            desk_state=_enum_value(desk.state),
            direction=_enum_value(desk.direction),
            target_height_cm=desk.target_height_cm,
            control_mode=_enum_value(automation.control_mode),
            automation_state=_enum_value(automation.state),
            activity_mode=(
                {"key": activity_mode.key, "name": activity_mode.name}
                if activity_mode is not None
                else None
            ),
            posture=_enum_value(automation.posture_candidate),
            blocked_reason_codes=list(automation.blocked_reason_codes),
        )

    @function_tool
    async def hold_desk(
        ctx: RunContextWrapper[SmartDeskAgentContext],
        direction: Annotated[str, Field(pattern="^(up|down)$")],
    ) -> dict[str, object]:
        """Hold the desk moving up or down until the user stops it."""
        context = await guarded(ctx)
        if context is None:
            return _error("session_mismatch")
        try:
            # 음성 제어는 얼굴 인식과 무관하게 받는다. None은 Dashboard/HTTP와 같은
            # identity 비의존 경로로, session 소유권 검사를 건너뛴다.
            await context.automation.hold(Direction(direction.upper()), expected_session_id=None)
            return _ok(direction=direction)
        except Exception:
            return _error("desk_command_failed")

    @function_tool
    async def set_desk_target(
        ctx: RunContextWrapper[SmartDeskAgentContext],
        height_cm: Annotated[float, Field(ge=40, le=130)],
    ) -> dict[str, object]:
        """Move the desk to a target height in centimetres."""
        context = await guarded(ctx)
        if context is None:
            return _error("session_mismatch")
        try:
            await context.automation.set_target(height_cm, expected_session_id=None)
            return _ok(height_cm=height_cm)
        except Exception:
            return _error("desk_command_failed")

    @function_tool
    async def adjust_desk_height(
        ctx: RunContextWrapper[SmartDeskAgentContext],
        delta_cm: Annotated[float, Field(ge=-30, le=30)],
    ) -> dict[str, object]:
        """Raise or lower the desk relative to its current height in centimetres."""
        context = await guarded(ctx)
        if context is None:
            return _error("session_mismatch")
        if context.dashboard is None:
            return _error("desk_status_unavailable")
        try:
            current_height = context.dashboard.get_status().height.height_cm
            if current_height is None:
                return _error("desk_height_unavailable")
            target_height = round(current_height + delta_cm, 1)
            await context.automation.set_target(target_height, expected_session_id=None)
            return _ok(
                previous_height_cm=current_height,
                delta_cm=delta_cm,
                target_height_cm=target_height,
            )
        except Exception:
            return _error("desk_command_failed")

    @function_tool
    async def set_control_mode(
        ctx: RunContextWrapper[SmartDeskAgentContext],
        mode: Annotated[str, Field(pattern="^(auto|manual)$")],
    ) -> dict[str, object]:
        """Set automatic or manual desk control mode."""
        context = await guarded(ctx)
        if context is None:
            return _error("session_mismatch")
        try:
            await context.automation.set_control_mode(ControlMode(mode.upper()), None)
            return _ok(mode=mode)
        except Exception:
            return _error("desk_command_failed")

    @function_tool
    async def set_activity_mode(
        ctx: RunContextWrapper[SmartDeskAgentContext],
        mode: Annotated[str, Field(min_length=1, max_length=80)],
    ) -> dict[str, object]:
        """Select an activity mode by its spoken name or key, such as '공부' or 'default'."""
        context = await guarded(ctx)
        if context is None:
            return _error("session_mismatch")
        if context.activity_modes is None:
            return _error("activity_modes_unavailable")
        profile_id = await context.personalization_profile_id()
        if profile_id is None:
            return _error("session_mismatch")
        try:
            modes = await context.activity_modes.list_effective_modes(profile_id)
            requested = mode.strip().casefold()
            matches = [
                item
                for item in modes
                if requested
                in {
                    item.key.casefold(),
                    item.name.casefold(),
                    f"{item.name} 모드".casefold(),
                }
            ]
            if len(matches) != 1:
                return _error("activity_mode_not_found")
            selected = matches[0]
            await context.automation.set_activity_mode(
                selected.key, context.turn_context.session_id, profile_id=profile_id
            )
            return _ok(key=selected.key, name=selected.name)
        except Exception:
            return _error("desk_command_failed")

    @function_tool
    async def list_activity_modes(ctx: RunContextWrapper[SmartDeskAgentContext]) -> dict[str, object]:
        """List the current user's activity modes and their names, keys, and settings."""
        context = ctx.context
        await context.tool_started()
        if context.activity_modes is None:
            return _error("activity_modes_unavailable")
        profile_id = await context.personalization_profile_id()
        if profile_id is None:
            return _error("session_mismatch")
        try:
            modes = await context.activity_modes.list_effective_modes(profile_id)
        except Exception:
            return _error("activity_modes_failed")
        return _ok(modes=[
            {"key": mode.key, "name": mode.name, "description": mode.description,
             "led_color": mode.led_color, "led_brightness": mode.led_brightness,
             "tilt_level": mode.tilt_level}
            for mode in modes
        ])

    @function_tool
    async def get_activity_usage(
        ctx: RunContextWrapper[SmartDeskAgentContext],
        days: Annotated[int, Field(ge=1, le=31)] = 1,
    ) -> dict[str, object]:
        """Summarize the current user's activity-mode usage for the last 1 to 31 days."""
        context = ctx.context
        await context.tool_started()
        if context.mode_usage is None:
            return _error("activity_usage_unavailable")
        profile_id = await context.personalization_profile_id()
        if profile_id is None:
            return _error("session_mismatch")
        try:
            summary = await context.mode_usage.summarize(
                days=days, profile_id=profile_id
            )
        except Exception:
            return _error("activity_usage_failed")
        return {"ok": True, "result": summary}

    @function_tool
    async def get_tilt_state(ctx: RunContextWrapper[SmartDeskAgentContext]) -> dict[str, object]:
        """Read the desk-top tilt: its current level, target, and the allowed range."""
        context = ctx.context
        await context.tool_started()
        if context.tilt is None:
            return _error("tilt_unavailable")
        try:
            snapshot = context.tilt.get_snapshot()
        except Exception:
            return _error("tilt_command_failed")
        minimum, maximum = context.tilt_level_range
        return _ok(
            state=str(snapshot.state), level=snapshot.level, target_level=snapshot.target_level,
            position_known=snapshot.position_valid, min_level=minimum, max_level=maximum,
        )

    @function_tool
    async def set_tilt_level(
        ctx: RunContextWrapper[SmartDeskAgentContext],
        level: Annotated[int, Field(ge=0, le=10)],
    ) -> dict[str, object]:
        """Tilt the desk top to a level. The lowest level also re-zeroes the angle."""
        context = await guarded(ctx)
        if context is None:
            return _error("session_mismatch")
        if context.tilt is None:
            return _error("tilt_unavailable")
        minimum, maximum = context.tilt_level_range
        if not minimum <= level <= maximum:
            return _error("tilt_level_out_of_range")
        try:
            await context.tilt.set_target(level)
            return _ok(level=level)
        except Exception:
            return _error("tilt_command_failed")

    @function_tool
    async def stop_tilt(ctx: RunContextWrapper[SmartDeskAgentContext]) -> dict[str, object]:
        """Immediately stop tilt motion. This safety command is always allowed."""
        await ctx.context.tool_started()
        if ctx.context.tilt is None:
            return _error("tilt_unavailable")
        try:
            await ctx.context.tilt.stop_motion("음성으로 틸팅을 정지했습니다.")
            return _ok(action="stopped")
        except Exception:
            return _error("tilt_command_failed")

    @function_tool
    async def get_wled_state(ctx: RunContextWrapper[SmartDeskAgentContext]) -> dict[str, object]:
        """Read the current WLED state."""
        await ctx.context.tool_started()
        if ctx.context.wled is None:
            return _error("wled_unavailable")
        try:
            value = await ctx.context.wled.refresh_state()
            return _ok(on=value.on, brightness=value.brightness, mode=value.mode.value if value.mode else None)
        except Exception:
            return _error("wled_unavailable")

    @function_tool
    async def get_wled_capabilities(ctx: RunContextWrapper[SmartDeskAgentContext]) -> dict[str, object]:
        """Read supported WLED effects and palettes."""
        await ctx.context.tool_started()
        if ctx.context.wled is None:
            return _error("wled_unavailable")
        try:
            value = await ctx.context.wled.refresh_capabilities()
            return _ok(effects=[{"id": x.id, "name": x.name} for x in value.effects], palettes=[{"id": x.id, "name": x.name} for x in value.palettes])
        except Exception:
            return _error("wled_unavailable")

    async def wled_mutate(ctx: RunContextWrapper[SmartDeskAgentContext], method: str, *args: Any, **kwargs: Any) -> dict[str, object]:
        context = await guarded(ctx)
        if context is None:
            return _error("session_mismatch")
        if context.wled is None:
            return _error("wled_unavailable")
        try:
            # 조명도 기기 제어다. 얼굴 인식과 무관하게 받는다.
            value = await getattr(context.wled, method)(*args, expected_session_id=None, **kwargs)
            return _ok(on=value.on, brightness=value.brightness, mode=value.mode.value if value.mode else None)
        except Exception:
            return _error("wled_command_failed")

    @function_tool
    async def turn_wled_off(ctx: RunContextWrapper[SmartDeskAgentContext]) -> dict[str, object]:
        """Turn the WLED lighting off without changing saved profile settings."""
        return await wled_mutate(ctx, "turn_off")

    @function_tool
    async def turn_wled_on(ctx: RunContextWrapper[SmartDeskAgentContext]) -> dict[str, object]:
        """Turn the WLED lighting on without changing saved profile settings."""
        return await wled_mutate(ctx, "turn_on")

    @function_tool
    async def set_wled_brightness(ctx: RunContextWrapper[SmartDeskAgentContext], brightness: Annotated[int, Field(ge=0, le=255)]) -> dict[str, object]:
        """Set WLED brightness from 0 to 255 without changing saved profile settings."""
        return await wled_mutate(ctx, "set_brightness", brightness)

    @function_tool
    async def set_wled_color(ctx: RunContextWrapper[SmartDeskAgentContext], color_hex: Annotated[str, Field(pattern="^[0-9A-Fa-f]{6}$")]) -> dict[str, object]:
        """Set a six-digit RGB WLED colour without changing saved profile settings."""
        return await wled_mutate(ctx, "set_solid", color_hex)

    @function_tool
    async def set_wled_effect(
        ctx: RunContextWrapper[SmartDeskAgentContext],
        effect_id: Annotated[int, Field(ge=0)],
        palette_id: Annotated[int, Field(ge=0)] = 0,
        speed: Annotated[int, Field(ge=0, le=255)] = 128,
        intensity: Annotated[int, Field(ge=0, le=255)] = 128,
        color_hex: Annotated[str | None, Field(pattern="^[0-9A-Fa-f]{6}$")] = None,
    ) -> dict[str, object]:
        """Set a WLED effect without changing the saved activity-mode settings."""
        return await wled_mutate(
            ctx,
            "set_effect",
            effect_id,
            palette_id=palette_id,
            speed=speed,
            intensity=intensity,
            color=color_hex,
        )

    @function_tool
    async def remember_fact(ctx: RunContextWrapper[SmartDeskAgentContext], fact: Annotated[str, Field(min_length=1, max_length=500)]) -> dict[str, object]:
        """Persist one user-confirmed fact and report the actual result."""
        context = ctx.context
        if not context.turn_context.personalized or not await context.valid_mutation():
            return _error("memory_not_available")
        await context.tool_started()
        try:
            await context.memory.remember(
                context.turn_context.profile_id,
                fact,
                explicit=True,
                is_valid=context.valid_mutation,
            )
        except Exception as error:
            code = getattr(error, "code", "profile_memory_add_failed")
            return _error(str(code))
        return _ok(saved=True)

    @function_tool
    async def recall_facts(
        ctx: RunContextWrapper[SmartDeskAgentContext],
        query: Annotated[str, Field(min_length=1, max_length=500)],
    ) -> dict[str, object]:
        """Look up what this registered user said before about one subject."""

        context = ctx.context
        # 읽기지만 turn 도중 사용자가 바뀌었을 수 있다. 남의 기억을 말하지 않도록
        # 쓰기와 같은 session 검사를 지난다.
        if not context.turn_context.personalized or not await context.valid_mutation():
            return _error("memory_not_available")
        await context.tool_started()
        try:
            values = await context.memory.search(context.turn_context.profile_id, query)
        except Exception as error:
            code = getattr(error, "code", "profile_memory_search_failed")
            return _error(str(code))
        facts = [
            value["memory"].strip()
            for value in values
            if isinstance(value.get("memory"), str) and value["memory"].strip()
        ]
        return _ok(facts=facts)

    @function_tool
    async def forget_fact(
        ctx: RunContextWrapper[SmartDeskAgentContext],
        fact: Annotated[str, Field(min_length=1, max_length=500)],
    ) -> dict[str, object]:
        """Delete one clearly identified remembered fact for the current registered user."""

        context = ctx.context
        if not context.turn_context.personalized or not await context.valid_mutation():
            return _error("memory_not_available")
        await context.tool_started()
        try:
            values = await context.memory.search(context.turn_context.profile_id, fact)
            normalized = fact.strip()
            matches = [
                value
                for value in values
                if isinstance(value.get("id"), str)
                and isinstance(value.get("memory"), str)
                and value["memory"].strip() == normalized
            ]
            if len(matches) != 1:
                return _error("memory_disambiguation_required")
            await context.memory.delete(
                context.turn_context.profile_id,
                matches[0]["id"],
                is_valid=context.valid_mutation,
            )
        except Exception as error:
            code = getattr(error, "code", "profile_memory_delete_failed")
            return _error(str(code))
        return _ok(deleted=True)

    @function_tool
    async def request_followup(ctx: RunContextWrapper[SmartDeskAgentContext]) -> dict[str, object]:
        """Request a short follow-up listening window only when another answer is needed."""
        context = ctx.context
        await context.tool_started()
        context.followup_requested = True
        return _ok(followup_requested=True)

    return [stop_desk, get_desk_status, hold_desk, set_desk_target,
            adjust_desk_height, set_control_mode,
            list_activity_modes, set_activity_mode, get_activity_usage,
            get_tilt_state, set_tilt_level, stop_tilt,
            get_wled_state, get_wled_capabilities, turn_wled_off, turn_wled_on,
            set_wled_brightness, set_wled_color, set_wled_effect,
            remember_fact, recall_facts, forget_fact,
            request_followup]
