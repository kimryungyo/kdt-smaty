"""SQLite 저장 기반의 공개 타입과 오류를 노출한다."""

from smart_desk.storage.sqlite import (
    SQLiteDatabase,
    StorageCorruptedError,
    StorageError,
    StorageNotReadyError,
    StorageOperationError,
    StorageVersionError,
)

__all__ = [
    "SQLiteDatabase",
    "StorageCorruptedError",
    "StorageError",
    "StorageNotReadyError",
    "StorageOperationError",
    "StorageVersionError",
]
