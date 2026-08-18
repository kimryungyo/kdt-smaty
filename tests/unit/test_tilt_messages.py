"""틸팅 MQTT 명령·상태 wire 계약 테스트."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from smart_desk.modules.tilt.messages import (
    TiltCommandAdapter,
    TiltGotoCommand,
    TiltStatusMessage,
    TiltStopCommand,
)
from smart_desk.modules.tilt.models import TiltState


def test_goto_and_stop_commands_are_strictly_parsed() -> None:
    assert isinstance(TiltCommandAdapter.validate_json(b'{"command":"GOTO","level":2}'), TiltGotoCommand)
    assert isinstance(TiltCommandAdapter.validate_json(b'{"command":"STOP"}'), TiltStopCommand)


@pytest.mark.parametrize(
    "payload",
    [b"{", b'{"command":"GOTO"}', b'{"command":"GOTO","level":-1}', b'{"command":"MOVE"}'],
)
def test_invalid_command_payloads_are_rejected(payload: bytes) -> None:
    with pytest.raises(ValidationError):
        TiltCommandAdapter.validate_json(payload)


def test_status_serializes_new_position_and_target_fields_in_camel_case() -> None:
    message = TiltStatusMessage(
        state=TiltState.MOVING,
        level=None,
        target_level=2,
        position_mm=38.0,
        position_valid=True,
        detail="2단계로 이동 중입니다.",
        updated_at=datetime(2026, 8, 19, tzinfo=UTC),
    )

    dumped = message.model_dump(by_alias=True)

    assert dumped["schema"] == "smartdesk.tilt.status.v1"
    assert dumped["targetLevel"] == 2
    assert dumped["positionMm"] == 38.0
    assert dumped["positionValid"] is True
    assert dumped["updatedAt"].tzinfo is not None


def test_status_requires_utc_timestamp() -> None:
    with pytest.raises(ValidationError):
        TiltStatusMessage(state=TiltState.IDLE, detail="테스트", updated_at=datetime(2026, 8, 19))
