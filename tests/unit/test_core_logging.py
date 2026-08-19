import json
import logging

from smart_desk.core.logging import JsonFormatter


def record(**extra: object) -> logging.LogRecord:
    item = logging.LogRecord("test", logging.WARNING, __file__, 1, "detail", None, None)
    for key, value in extra.items():
        setattr(item, key, value)
    return item


def test_diagnostic_fields_reach_the_log_line() -> None:
    payload = json.loads(JsonFormatter().format(record(
        component="voice", event="device_retry_scheduled",
        error_code="speaker_failed", from_state="RECORDING", to_state="ERROR",
        retry_seconds=30.0,
    )))
    assert payload["component"] == "voice"
    assert payload["event"] == "device_retry_scheduled"
    assert payload["error_code"] == "speaker_failed"
    assert (payload["from_state"], payload["to_state"]) == ("RECORDING", "ERROR")
    assert payload["retry_seconds"] == 30.0


def test_unlisted_extras_stay_out_of_the_log_line() -> None:
    payload = json.loads(JsonFormatter().format(record(
        component="voice", event="announcement_failed", transcript="사용자 발화",
    )))
    assert "transcript" not in payload
