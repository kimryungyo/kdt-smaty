"""얼굴 표본, 신원 및 current-user 공개 모델."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pydantic import BaseModel, ConfigDict

from smart_desk.modules.vision.models import IdentityStatus, _to_camel


@dataclass(frozen=True, slots=True)
class FaceEmbedding:
    model_name: str
    model_version: str
    dimension: int
    normalization: str
    created_at: datetime
    vector: tuple[float, ...]


class SessionKind(StrEnum):
    REGISTERED = "REGISTERED"
    ANONYMOUS = "ANONYMOUS"


@dataclass(frozen=True, slots=True)
class CurrentUserSnapshot:
    session_id: str
    kind: SessionKind
    profile_id: str | None
    started_at: datetime
    changed_at: datetime


@dataclass(frozen=True, slots=True)
class SessionChange:
    sequence: int
    previous_session_id: str | None
    current_session_id: str | None
    reason: str
    changed_at: datetime
    current: CurrentUserSnapshot | None


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        frozen=True,
        serialize_by_alias=True,
        validate_by_alias=True,
        validate_by_name=True,
    )


class CurrentUserResponse(ApiModel):
    session: "CurrentUserApi | None"


class CurrentUserApi(ApiModel):
    session_id: str
    kind: SessionKind
    profile_id: str | None
    started_at: datetime
    changed_at: datetime


class EnrollmentState(StrEnum):
    WAITING_FACE = "WAITING_FACE"
    CAPTURING = "CAPTURING"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class EnrollmentSnapshot(ApiModel):
    enrollment_id: str
    profile_id: str
    state: EnrollmentState
    required_samples: int
    accepted_samples: int
    started_at: datetime
    changed_at: datetime
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class IdentityObservation:
    status: IdentityStatus
    profile_id: str | None
    observed_at: datetime | None
    expires_at: datetime | None
