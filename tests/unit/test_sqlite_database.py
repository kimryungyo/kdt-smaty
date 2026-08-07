"""SQLite lifecycle, migration과 transaction 경계 테스트."""

import asyncio
from pathlib import Path
import sqlite3
import threading

import pytest

from smart_desk.storage import (
    SQLiteDatabase,
    StorageCorruptedError,
    StorageNotReadyError,
    StorageVersionError,
)


async def test_new_database_migrates_to_version_one(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "nested" / "smart-desk.db")

    await database.start()
    version, tables, columns, foreign_keys = await database.read(
        lambda connection: (
            connection.execute("PRAGMA user_version").fetchone()[0],
            [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_schema "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            ],
            [row[1] for row in connection.execute("PRAGMA table_info(profiles)")],
            connection.execute("PRAGMA foreign_keys").fetchone()[0],
        )
    )

    assert database.path == (tmp_path / "nested" / "smart-desk.db").resolve()
    assert version == 1
    assert tables == ["profiles"]
    assert columns == [
        "id",
        "name",
        "sitting_height_cm",
        "standing_height_cm",
        "led_color",
    ]
    assert foreign_keys == 1
    await database.stop()


async def test_relative_path_is_resolved_from_project_root() -> None:
    database = SQLiteDatabase(Path("data/smart_desk.db"))

    assert database.path == Path("/srv/smart-desk-fin/data/smart_desk.db")


async def test_start_is_idempotent_and_database_can_restart(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "smart-desk.db")

    await database.start()
    await database.start()
    await database.stop()
    await database.stop()
    await database.start()

    assert await database.read(lambda connection: 1) == 1
    await database.stop()


async def test_operations_are_rejected_before_start_and_after_stop(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "smart-desk.db")

    with pytest.raises(StorageNotReadyError):
        await database.read(lambda connection: None)

    await database.start()
    await database.stop()

    with pytest.raises(StorageNotReadyError):
        await database.write(lambda connection: None)


async def test_write_commits_and_callback_error_rolls_back(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "smart-desk.db")
    await database.start()

    await database.write(
        lambda connection: connection.execute(
            "INSERT INTO profiles VALUES (?, ?, ?, ?, ?)",
            ("profile-" + "1" * 32, "first", 80.0, 100.0, None),
        )
    )

    class CallbackError(RuntimeError):
        pass

    def failing_write(connection: sqlite3.Connection) -> None:
        connection.execute(
            "UPDATE profiles SET name = ? WHERE id = ?",
            ("changed", "profile-" + "1" * 32),
        )
        raise CallbackError("rollback")

    with pytest.raises(CallbackError, match="rollback"):
        await database.write(failing_write)

    name = await database.read(
        lambda connection: connection.execute(
            "SELECT name FROM profiles WHERE id = ?",
            ("profile-" + "1" * 32,),
        ).fetchone()[0]
    )
    assert name == "first"
    await database.stop()


async def test_operations_are_serialized_and_cancellation_waits_for_worker(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "smart-desk.db")
    await database.start()
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def slow_read(_connection: sqlite3.Connection) -> None:
        first_entered.set()
        release_first.wait(timeout=2)

    def second_read(_connection: sqlite3.Connection) -> None:
        second_entered.set()

    first = asyncio.create_task(database.read(slow_read))
    await asyncio.to_thread(first_entered.wait, 1)
    first.cancel()
    second = asyncio.create_task(database.read(second_read))
    await asyncio.sleep(0.05)
    assert not second_entered.is_set()

    release_first.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    assert second_entered.is_set()
    await database.stop()


async def test_non_sqlite_file_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "smart-desk.db"
    original = b"not a sqlite database"
    path.write_bytes(original)
    database = SQLiteDatabase(path)

    with pytest.raises(StorageCorruptedError):
        await database.start()

    assert path.read_bytes() == original
    with pytest.raises(StorageNotReadyError):
        await database.read(lambda connection: None)


@pytest.mark.parametrize("version", [2, 100])
async def test_future_schema_version_is_not_downgraded(
    tmp_path: Path,
    version: int,
) -> None:
    path = tmp_path / "smart-desk.db"
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version = {version}")
    database = SQLiteDatabase(path)

    with pytest.raises(StorageVersionError):
        await database.start()

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == version


async def test_version_one_schema_mismatch_is_not_repaired(tmp_path: Path) -> None:
    path = tmp_path / "smart-desk.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE profiles (id TEXT PRIMARY KEY)")
        connection.execute("PRAGMA user_version = 1")
    database = SQLiteDatabase(path)

    with pytest.raises(StorageVersionError):
        await database.start()

    with sqlite3.connect(path) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(profiles)")]
    assert columns == ["id"]


async def test_version_one_missing_checks_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "smart-desk.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE profiles ("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL, "
            "sitting_height_cm REAL NOT NULL, standing_height_cm REAL NOT NULL, "
            "led_color TEXT)"
        )
        connection.execute("PRAGMA user_version = 1")
    database = SQLiteDatabase(path)

    with pytest.raises(StorageVersionError):
        await database.start()

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 0
