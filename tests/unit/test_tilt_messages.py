"""틸팅 MQTT GOTO/STOP 판별 유니온과 상태 발행 계약 테스트."""

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


def test_goto_command_parses_from_command_field() -> None:
    payload = b'{"schema":"smartdesk.tilt.command.v1","command":"GOTO","level":2}'

    command = TiltCommandAdapter.validate_json(payload)

    assert isinstance(command, TiltGotoCommand)
    assert command.level == 2


def test_stop_command_parses_from_command_field() -> None:
    payload = b'{"schema":"smartdesk.tilt.command.v1","command":"STOP"}'

    command = TiltCommandAdapter.validate_json(payload)

    assert isinstance(command, TiltStopCommand)


@pytest.mark.parametrize(
    "payload",
    [
        b"{",
        b'{"command":"GOTO"}',
        b'{"command":"GOTO","level":-1}',
        b'{"command":"GOTO","level":1.5}',
        b'{"command":"MOVE"}',
        b'{"command":"GOTO","level":1,"extra":true}',
    ],
)
def test_invalid_command_payloads_are_rejected(payload: bytes) -> None:
    with pytest.raises(ValidationError):
        TiltCommandAdapter.validate_json(payload)


def test_status_message_requires_utc_timestamp() -> None:
    with pytest.raises(ValidationError):
        TiltStatusMessage(
            state=TiltState.IDLE,
            detail="테스트",
            updated_at=datetime(2026, 8, 18),
        )


def test_status_message_serializes_schema_alias() -> None:
    message = TiltStatusMessage(
        state=TiltState.MOVING,
        level=2,
        position_mm=73.33,
        firmware="tilt-hw039-1.0.1",
        detail="2단계로 이동합니다.",
        updated_at=datetime(2026, 8, 18, tzinfo=UTC),
    )

    dumped = message.model_dump(by_alias=True)

    assert dumped["schema"] == "smartdesk.tilt.status.v1"
    assert dumped["state"] == "MOVING"
    assert dumped["level"] == 2
