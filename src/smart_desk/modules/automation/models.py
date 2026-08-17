"""Public immutable state and HTTP contracts for desk automation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from smart_desk.modules.profiles.models import EffectiveActivityMode
from smart_desk.modules.vision.models import PostureStatus, _to_camel


class ControlMode(StrEnum):
    AUTO = "AUTO"
    MANUAL = "MANUAL"


class AutomationState(StrEnum):
    WAITING_USER = "WAITING_USER"
    OBSERVING = "OBSERVING"
    READY = "READY"
    MOVING = "MOVING"
    MANUAL = "MANUAL"
    BLOCKED = "BLOCKED"
    PARK_WAITING = "PARK_WAITING"
    PARKING = "PARKING"


class HeightPolicy(StrEnum):
    PROFILE_ACTIVITY_MODE = "PROFILE_ACTIVITY_MODE"
    ANONYMOUS_DEFAULT = "ANONYMOUS_DEFAULT"
    PARK = "PARK"


class IntentSource(StrEnum):
    AUTO = "AUTO"
    PARK = "PARK"
    MANUAL = "MANUAL"


@dataclass(frozen=True, slots=True)
class AutomationSnapshot:
    session_id: str | None
    control_mode: ControlMode | None
    activity_mode: EffectiveActivityMode | None
    state: AutomationState
    height_policy: HeightPolicy | None
    posture_candidate: PostureStatus | None
    candidate_since: datetime | None
    target_height_cm: float | None
    intent_source: IntentSource | None
    blocked_reason_codes: tuple[str, ...]
    initial_move_due_at: datetime | None
    park_due_at: datetime | None
    generation: int
    revision: int
    last_transition_reason: str
    last_transition_source: str
    last_transition_at: datetime
    updated_at: datetime


class AutomationApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        frozen=True,
        serialize_by_alias=True,
        validate_by_alias=True,
        validate_by_name=True,
    )


class AutomationStatusResponse(AutomationApiModel):
    session_id: str | None
    control_mode: ControlMode | None
    activity_mode: EffectiveActivityMode | None
    state: AutomationState
    height_policy: HeightPolicy | None
    posture_candidate: PostureStatus | None
    candidate_since: datetime | None
    target_height_cm: float | None
    intent_source: IntentSource | None
    blocked_reason_codes: list[str]
    initial_move_due_at: datetime | None
    park_due_at: datetime | None
    generation: int
    revision: int
    last_transition_reason: str
    last_transition_source: str
    last_transition_at: datetime
    updated_at: datetime

    @classmethod
    def from_snapshot(cls, item: AutomationSnapshot) -> "AutomationStatusResponse":
        return cls(
            session_id=item.session_id, control_mode=item.control_mode,
            activity_mode=item.activity_mode, state=item.state,
            height_policy=item.height_policy, posture_candidate=item.posture_candidate,
            candidate_since=item.candidate_since, target_height_cm=item.target_height_cm,
            intent_source=item.intent_source,
            blocked_reason_codes=list(item.blocked_reason_codes),
            initial_move_due_at=item.initial_move_due_at, park_due_at=item.park_due_at,
            generation=item.generation, revision=item.revision,
            last_transition_reason=item.last_transition_reason,
            last_transition_source=item.last_transition_source,
            last_transition_at=item.last_transition_at, updated_at=item.updated_at,
        )


class ControlModeRequest(AutomationApiModel):
    control_mode: ControlMode
    expected_session_id: str = Field(min_length=1)


class ActivityModeRequest(AutomationApiModel):
    activity_mode_key: str = Field(min_length=1)
    expected_session_id: str = Field(min_length=1)
