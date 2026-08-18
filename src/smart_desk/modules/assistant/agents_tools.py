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
    followup_requested: bool = False
    assistant_response: str = ""

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


def build_smart_desk_tools() -> list[Any]:
    """Build the local SDK function tools for a per-turn context."""

    async def guarded(ctx: RunContextWrapper[SmartDeskAgentContext]) -> SmartDeskAgentContext | None:
        context = ctx.context
        if not await context.valid_mutation():
            return None
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
    async def hold_desk(
        ctx: RunContextWrapper[SmartDeskAgentContext],
        direction: Annotated[str, Field(pattern="^(up|down)$")],
    ) -> dict[str, object]:
        """Hold the desk moving up or down until the user stops it."""
        context = await guarded(ctx)
        if context is None:
            return _error("session_mismatch")
        try:
            await context.automation.hold(
                Direction(direction.upper()), expected_session_id=context.turn_context.session_id
            )
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
            await context.automation.set_target(height_cm, expected_session_id=context.turn_context.session_id)
            return _ok(height_cm=height_cm)
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
            await context.automation.set_control_mode(ControlMode(mode.upper()), context.turn_context.session_id)
            return _ok(mode=mode)
        except Exception:
            return _error("desk_command_failed")

    @function_tool
    async def set_activity_mode(
        ctx: RunContextWrapper[SmartDeskAgentContext], key: Annotated[str, Field(min_length=1, max_length=80)]
    ) -> dict[str, object]:
        """Select the current registered user's activity-mode key."""
        context = await guarded(ctx)
        if context is None:
            return _error("session_mismatch")
        try:
            await context.automation.set_activity_mode(key, context.turn_context.session_id)
            return _ok(key=key)
        except Exception:
            return _error("desk_command_failed")

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
            value = await getattr(context.wled, method)(*args, expected_session_id=context.turn_context.session_id, **kwargs)
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
        if not await context.valid_mutation():
            return _error("session_mismatch")
        await context.tool_started()
        context.followup_requested = True
        return _ok(followup_requested=True)

    return [stop_desk, hold_desk, set_desk_target, set_control_mode, set_activity_mode,
            get_wled_state, get_wled_capabilities, turn_wled_off, turn_wled_on,
            set_wled_brightness, set_wled_color, set_wled_effect, remember_fact, forget_fact,
            request_followup]
