"""표준 출력에 구조화된 JSON 로그를 기록한다."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
import sys
from typing import Any


class JsonFormatter(logging.Formatter):
    """운영 로그의 공통 필드를 JSON 객체로 변환한다."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "component": getattr(record, "component", record.name),
            "event": getattr(record, "event", "log"),
            "detail": record.getMessage(),
        }
        if hasattr(record, "task_name"):
            payload["task_name"] = record.task_name
        if hasattr(record, "resource"):
            payload["resource"] = record.resource
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str) -> None:
    """애플리케이션 root logger를 지정한 수준으로 한 번 구성한다."""

    root = logging.getLogger()
    root.setLevel(level)
    if any(getattr(handler, "_smart_desk_handler", False) for handler in root.handlers):
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._smart_desk_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)

