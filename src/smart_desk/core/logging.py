"""표준 출력에 구조화된 JSON 로그를 기록한다."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
import sys
from typing import Any


# 로그로 내보내도 되는 진단 필드. 사용자 발화나 provider 본문은 담지 않고,
# 상태 이름과 오류 code 같은 고정 어휘만 담는다.
DIAGNOSTIC_FIELDS = (
    "task_name", "resource", "error_code", "from_state", "to_state",
    "retry_seconds", "timeout_seconds",
)


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
        for field in DIAGNOSTIC_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
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

