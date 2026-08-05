"""애플리케이션 준비 상태를 thread-safe snapshot으로 관리한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock


class ApplicationStatus(StrEnum):
    """애플리케이션 수명주기의 현재 단계다."""

    CREATED = "CREATED"
    STARTING = "STARTING"
    READY = "READY"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    """외부에 노출하는 불변 애플리케이션 상태다."""

    status: ApplicationStatus
    detail: str
    updated_at: datetime

    @property
    def ready(self) -> bool:
        """애플리케이션이 요청을 처리할 준비가 됐는지 반환한다."""

        return self.status is ApplicationStatus.READY


class RuntimeState:
    """프로세스의 현재 수명주기 상태를 동기화해 보관한다."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._snapshot = RuntimeSnapshot(
            status=ApplicationStatus.CREATED,
            detail="애플리케이션이 생성되었습니다.",
            updated_at=datetime.now(UTC),
        )

    def snapshot(self) -> RuntimeSnapshot:
        """현재 상태의 불변 snapshot을 반환한다."""

        with self._lock:
            return self._snapshot

    def mark(self, status: ApplicationStatus, detail: str) -> None:
        """상태와 설명을 현재 UTC 시각으로 갱신한다."""

        with self._lock:
            self._snapshot = RuntimeSnapshot(
                status=status,
                detail=detail,
                updated_at=datetime.now(UTC),
            )

    def mark_failed(self, detail: str) -> None:
        """필수 작업 실패로 애플리케이션을 준비되지 않은 상태로 바꾼다."""

        self.mark(ApplicationStatus.FAILED, detail)

