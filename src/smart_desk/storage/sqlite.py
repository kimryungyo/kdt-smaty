"""SQLite 연결, migration, transaction과 비동기 실행 경계를 관리한다."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
import os
from pathlib import Path
import sqlite3
from sqlite3 import Connection
from typing import TypeVar


LOGGER = logging.getLogger(__name__)
CURRENT_SCHEMA_VERSION = 2
SQLITE_TIMEOUT_SECONDS = 5.0
PROJECT_ROOT = Path(__file__).resolve().parents[3]

T = TypeVar("T")
DatabaseOperation = Callable[[Connection], T]


class StorageError(RuntimeError):
    """SQLite 저장 기반에서 발생한 오류다."""


class StorageNotReadyError(StorageError):
    """시작되지 않았거나 종료 중인 저장소 접근 오류다."""


class StorageCorruptedError(StorageError):
    """SQLite 파일 형식 또는 내부 구조 손상 오류다."""


class StorageVersionError(StorageError):
    """지원하지 않는 version 또는 schema 오류다."""


class StorageOperationError(StorageError):
    """시작된 SQLite 저장소의 operation 실행 오류다."""


class SQLiteDatabase:
    """SQLite lifecycle과 짧은 read/write operation을 직렬화한다."""

    def __init__(self, configured_path: Path) -> None:
        self._path = _resolve_database_path(configured_path)
        self._operation_lock = asyncio.Lock()
        self._started = False
        self._closing = False

    @property
    def path(self) -> Path:
        """프로젝트 루트를 기준으로 해석한 절대 DB 경로를 반환한다."""

        return self._path

    async def start(self) -> None:
        """DB를 준비하고 현재 schema를 검증한 뒤 operation을 허용한다."""

        async with self._operation_lock:
            if self._started:
                return
            self._closing = False
            try:
                await _await_completion(asyncio.to_thread(self._start_sync))
            except sqlite3.Error as error:
                if _is_corruption_error(error):
                    raise StorageCorruptedError(
                        "SQLite 데이터베이스가 손상되었거나 올바른 형식이 아닙니다."
                    ) from error
                raise StorageOperationError(
                    "SQLite 데이터베이스를 시작하지 못했습니다."
                ) from error
            self._started = True

    async def stop(self) -> None:
        """진행 중인 operation 완료를 기다린 뒤 새 접근을 거부한다."""

        self._closing = True
        await _await_completion(self._finish_stop())

    async def _finish_stop(self) -> None:
        async with self._operation_lock:
            self._started = False
            self._closing = False

    async def read(self, operation: DatabaseOperation[T]) -> T:
        """명시적 write transaction 없이 callback을 worker에서 실행한다."""

        return await self._run_operation(operation, write=False)

    async def write(self, operation: DatabaseOperation[T]) -> T:
        """callback을 BEGIN IMMEDIATE transaction 안에서 실행한다."""

        return await self._run_operation(operation, write=True)

    async def _run_operation(
        self,
        operation: DatabaseOperation[T],
        *,
        write: bool,
    ) -> T:
        if not self._started or self._closing:
            raise StorageNotReadyError("SQLite 데이터베이스가 준비되지 않았습니다.")

        async with self._operation_lock:
            if not self._started or self._closing:
                raise StorageNotReadyError("SQLite 데이터베이스가 준비되지 않았습니다.")
            try:
                return await _await_completion(
                    asyncio.to_thread(self._execute_sync, operation, write=write)
                )
            except sqlite3.Error as error:
                raise StorageOperationError(
                    "SQLite 데이터베이스 operation을 완료하지 못했습니다."
                ) from error

    def _start_sync(self) -> None:
        parent_created = not self._path.parent.exists()
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if parent_created:
            _apply_permissions(self._path.parent, 0o700, "database_directory")

        file_existed = self._path.exists()
        connection = self._connect()
        if not file_existed and self._path.is_file():
            _apply_permissions(self._path, 0o600, "database_file")
        try:
            _verify_quick_check(connection)
            version = _read_user_version(connection)
            if version > CURRENT_SCHEMA_VERSION:
                raise StorageVersionError(
                    "현재 코드보다 새로운 SQLite schema version입니다."
                )
            if version == 0:
                _migrate_to_version_1(connection)
                version = 1
            if version == 1:
                _migrate_to_version_2(connection)
            _verify_version_2_schema(connection)
        finally:
            connection.close()

    def _execute_sync(
        self,
        operation: DatabaseOperation[T],
        *,
        write: bool,
    ) -> T:
        connection = self._connect()
        try:
            if not write:
                return operation(connection)

            connection.execute("BEGIN IMMEDIATE")
            try:
                result = operation(connection)
                connection.execute("COMMIT")
                return result
            except BaseException:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    LOGGER.exception(
                        "SQLite transaction rollback에 실패했습니다.",
                        extra={
                            "component": "sqlite",
                            "event": "rollback_failed",
                        },
                    )
                raise
        finally:
            connection.close()

    def _connect(self) -> Connection:
        connection = sqlite3.connect(
            self._path,
            timeout=SQLITE_TIMEOUT_SECONDS,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection


async def _await_completion(awaitable: Awaitable[T]) -> T:
    """호출 task가 취소돼도 worker 정리가 끝난 뒤 취소를 전달한다."""

    worker = asyncio.create_task(awaitable)
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if worker.done() and not worker.cancelled():
            try:
                worker.result()
            except BaseException:
                # 원래 호출의 cancellation을 우선하되 worker 예외는 회수한다.
                pass
        raise


def _resolve_database_path(configured_path: Path) -> Path:
    path = configured_path.expanduser()
    if path.is_absolute():
        return path.resolve()
    return (PROJECT_ROOT / path).resolve()


def _verify_quick_check(connection: Connection) -> None:
    rows = connection.execute("PRAGMA quick_check").fetchall()
    if not rows or any(row[0] != "ok" for row in rows):
        raise StorageCorruptedError("SQLite quick check에 실패했습니다.")


def _read_user_version(connection: Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    if row is None:
        raise StorageVersionError("SQLite schema version을 읽을 수 없습니다.")
    return int(row[0])


def _migrate_to_version_1(connection: Connection) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            CREATE TABLE profiles (
                id                  TEXT PRIMARY KEY,
                name                TEXT NOT NULL
                                    CHECK (
                                        typeof(name) = 'text'
                                        AND length(trim(name)) > 0
                                    ),
                sitting_height_cm   REAL NOT NULL
                                    CHECK (
                                        typeof(sitting_height_cm) IN ('integer', 'real')
                                        AND sitting_height_cm BETWEEN 75 AND 115
                                    ),
                standing_height_cm  REAL NOT NULL
                                    CHECK (
                                        typeof(standing_height_cm) IN ('integer', 'real')
                                        AND standing_height_cm BETWEEN 75 AND 115
                                    ),
                led_color           TEXT
                                    CHECK (
                                        led_color IS NULL
                                        OR (
                                            typeof(led_color) = 'text'
                                            AND length(led_color) = 6
                                            AND led_color NOT GLOB '*[^0-9A-F]*'
                                        )
                                    )
            )
            """
        )
        connection.execute("PRAGMA user_version = 1")
        connection.execute("COMMIT")
    except BaseException:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            LOGGER.exception(
                "SQLite migration rollback에 실패했습니다.",
                extra={"component": "sqlite", "event": "migration_rollback_failed"},
            )
        raise


def _migrate_to_version_2(connection: Connection) -> None:
    """기존 profile DB에 마지막 검증 높이 cache table을 추가한다."""

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            CREATE TABLE desk_height_cache (
                id          INTEGER PRIMARY KEY CHECK (id = 1),
                height_cm   REAL NOT NULL
                            CHECK (
                                typeof(height_cm) IN ('integer', 'real')
                                AND height_cm >= 73
                                AND height_cm <= 118
                            ),
                observed_at TEXT NOT NULL
                            CHECK (
                                typeof(observed_at) = 'text'
                                AND length(trim(observed_at)) > 0
                            )
            )
            """
        )
        connection.execute("PRAGMA user_version = 2")
        connection.execute("COMMIT")
    except BaseException:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            LOGGER.exception(
                "SQLite migration rollback에 실패했습니다.",
                extra={"component": "sqlite", "event": "migration_rollback_failed"},
            )
        raise


def _verify_version_2_schema(connection: Connection) -> None:
    version = _read_user_version(connection)
    if version != CURRENT_SCHEMA_VERSION:
        raise StorageVersionError("지원하지 않는 SQLite schema version입니다.")

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if tables != {"profiles", "desk_height_cache"}:
        raise StorageVersionError("SQLite version 2 table 구성이 올바르지 않습니다.")

    columns = connection.execute("PRAGMA table_info(profiles)").fetchall()
    actual = [
        (row["name"], row["type"], row["notnull"], row["pk"])
        for row in columns
    ]
    expected = [
        ("id", "TEXT", 0, 1),
        ("name", "TEXT", 1, 0),
        ("sitting_height_cm", "REAL", 1, 0),
        ("standing_height_cm", "REAL", 1, 0),
        ("led_color", "TEXT", 0, 0),
    ]
    if actual != expected:
        raise StorageVersionError("SQLite version 2 profiles schema가 올바르지 않습니다.")

    _verify_profile_constraints(connection)
    _verify_height_cache_schema(connection)


def _verify_height_cache_schema(connection: Connection) -> None:
    columns = connection.execute("PRAGMA table_info(desk_height_cache)").fetchall()
    actual = [(row["name"], row["type"], row["notnull"], row["pk"]) for row in columns]
    expected = [
        ("id", "INTEGER", 0, 1),
        ("height_cm", "REAL", 1, 0),
        ("observed_at", "TEXT", 1, 0),
    ]
    if actual != expected:
        raise StorageVersionError("SQLite version 2 height cache schema가 올바르지 않습니다.")

    connection.execute("SAVEPOINT validate_height_cache_schema")
    try:
        connection.execute(
            "INSERT INTO desk_height_cache VALUES (1, 80.0, '2026-08-15T00:00:00Z')"
        )
        for row in ((2, 80.0, "valid"), (1, 72.9, "valid"), (1, 118.1, "valid")):
            try:
                connection.execute("INSERT INTO desk_height_cache VALUES (?, ?, ?)", row)
            except sqlite3.IntegrityError:
                continue
            raise StorageVersionError("SQLite version 2 height cache 제약이 누락되었습니다.")
    finally:
        connection.execute("ROLLBACK TO validate_height_cache_schema")
        connection.execute("RELEASE validate_height_cache_schema")


def _verify_profile_constraints(connection: Connection) -> None:
    """version 1의 CHECK/unique 의미를 저장 변경 없이 확인한다."""

    valid_first = ("__schema_check_1__", "schema check", 115.0, 75.0, "A0B1C2")
    valid_second = ("__schema_check_2__", "schema check", 75.0, 115.0, None)
    invalid_rows = [
        ("__schema_check_3__", "   ", 80.0, 100.0, None),
        ("__schema_check_4__", "low sitting", 74.9, 100.0, None),
        ("__schema_check_5__", "high standing", 80.0, 115.1, None),
        ("__schema_check_6__", "lowercase led", 80.0, 100.0, "a0b1c2"),
    ]
    insert_sql = (
        "INSERT INTO profiles "
        "(id, name, sitting_height_cm, standing_height_cm, led_color) "
        "VALUES (?, ?, ?, ?, ?)"
    )

    connection.execute("SAVEPOINT validate_profiles_schema")
    try:
        try:
            connection.execute(insert_sql, valid_first)
            connection.execute(insert_sql, valid_second)
        except sqlite3.Error as error:
            raise StorageVersionError(
                "SQLite version 1 profiles schema 제약이 호환되지 않습니다."
            ) from error

        for row in invalid_rows:
            try:
                connection.execute(insert_sql, row)
            except sqlite3.IntegrityError:
                continue
            raise StorageVersionError(
                "SQLite version 1 profiles schema 제약이 누락되었습니다."
            )
    finally:
        connection.execute("ROLLBACK TO validate_profiles_schema")
        connection.execute("RELEASE validate_profiles_schema")


def _is_corruption_error(error: sqlite3.Error) -> bool:
    code = getattr(error, "sqlite_errorcode", None)
    return code in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}


def _apply_permissions(path: Path, mode: int, target: str) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        LOGGER.warning(
            "SQLite 경로 권한을 적용하지 못했습니다.",
            exc_info=True,
            extra={
                "component": "sqlite",
                "event": "permission_update_failed",
                "target": target,
            },
        )
