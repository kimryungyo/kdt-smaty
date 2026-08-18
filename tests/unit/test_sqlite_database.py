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
from smart_desk.storage.sqlite import (
    _migrate_to_version_1,
    _migrate_to_version_2,
    _migrate_to_version_3,
    _migrate_to_version_4,
    _migrate_to_version_5,
)


async def test_new_database_migrates_to_version_nine(tmp_path: Path) -> None:
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
    assert version == 9
    assert tables == ["profiles", "desk_height_cache", "profile_modes", "face_embeddings", "activity_mode_usage"]
    assert columns == [
        "id",
        "name",
        "sitting_height_cm",
        "standing_height_cm",
        "led_color",
        "tilt_level",
        "description",
        "pin_hash",
        "led_brightness",
        "led_schedule",
    ]
    assert foreign_keys == 1
    await database.stop()


async def test_interrupted_new_database_migration_resumes_from_version_one(
    tmp_path: Path,
) -> None:
    path = tmp_path / "smart-desk.db"
    with sqlite3.connect(path, isolation_level=None) as connection:
        _migrate_to_version_1(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_schema "
            "WHERE type = 'table' AND name = 'desk_height_cache'"
        ).fetchone()[0] == 0

    database = SQLiteDatabase(path)
    await database.start()
    version, cache_table_count = await database.read(
        lambda connection: (
            connection.execute("PRAGMA user_version").fetchone()[0],
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_schema "
                "WHERE type = 'table' AND name = 'desk_height_cache'"
            ).fetchone()[0],
        )
    )

    assert version == 9
    assert cache_table_count == 1
    await database.stop()


async def test_relative_path_is_resolved_from_project_root() -> None:
    database = SQLiteDatabase(Path("data/smart_desk.db"))

    assert database.path == Path(__file__).resolve().parents[2] / "data/smart_desk.db"


async def test_start_is_idempotent_and_database_can_restart(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "smart-desk.db")

    await database.start()
    await database.start()
    await database.stop()
    await database.stop()
    await database.start()

    assert await database.read(lambda connection: 1) == 1
    await database.stop()


async def test_restart_preserves_existing_height_cache(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "smart-desk.db")
    cached = (1, 73.9, "2026-08-15T10:37:18.592926Z")

    await database.start()
    await database.write(
        lambda connection: connection.execute(
            "INSERT INTO desk_height_cache VALUES (?, ?, ?)", cached
        )
    )
    await database.stop()

    await database.start()
    restored = await database.read(
        lambda connection: tuple(
            connection.execute(
                "SELECT id, height_cm, observed_at FROM desk_height_cache"
            ).fetchone()
        )
    )

    assert restored == cached
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
            "INSERT INTO profiles (id, name, sitting_height_cm, standing_height_cm, led_color) "
            "VALUES (?, ?, ?, ?, ?)",
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


@pytest.mark.parametrize("version", [3, 100])
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


async def test_version_two_data_migrates_once_to_profile_modes_without_loss(
    tmp_path: Path,
) -> None:
    path = tmp_path / "smart-desk.db"
    profile = ("profile-" + "a" * 32, "기존 사용자", 80.0, 105.0, "FF3000")
    cache = (1, 91.2, "2026-08-17T00:00:00Z")
    with sqlite3.connect(path, isolation_level=None) as connection:
        _migrate_to_version_1(connection)
        _migrate_to_version_2(connection)
        connection.execute("INSERT INTO profiles (id, name, sitting_height_cm, standing_height_cm, led_color) "
                "VALUES (?, ?, ?, ?, ?)", profile)
        connection.execute("INSERT INTO desk_height_cache VALUES (?, ?, ?)", cache)

    database = SQLiteDatabase(path)
    await database.start()
    version, restored_profile, restored_cache, mode_count = await database.read(
        lambda connection: (
            connection.execute("PRAGMA user_version").fetchone()[0],
            tuple(connection.execute("SELECT * FROM profiles").fetchone()),
            tuple(connection.execute("SELECT * FROM desk_height_cache").fetchone()),
            connection.execute("SELECT COUNT(*) FROM profile_modes").fetchone()[0],
        )
    )

    assert version == 9
    assert restored_profile == profile + (None, None, None, None, None)
    assert restored_cache == cache
    assert mode_count == 0
    await database.stop()


async def test_version_three_migration_rolls_back_when_index_creation_fails(
    tmp_path: Path,
) -> None:
    path = tmp_path / "smart-desk.db"
    profile = ("profile-" + "b" * 32, "보존", 80.0, 100.0, None)
    with sqlite3.connect(path, isolation_level=None) as connection:
        _migrate_to_version_1(connection)
        _migrate_to_version_2(connection)
        connection.execute("INSERT INTO profiles (id, name, sitting_height_cm, standing_height_cm, led_color) "
                "VALUES (?, ?, ?, ?, ?)", profile)
        connection.execute("CREATE INDEX profile_modes_profile_id_idx ON profiles(name)")

    database = SQLiteDatabase(path)
    with pytest.raises(Exception):
        await database.start()

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert tuple(connection.execute("SELECT * FROM profiles").fetchone()) == profile
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_schema WHERE type = 'table' AND name = 'profile_modes'"
        ).fetchone()[0] == 0


async def test_profile_mode_foreign_key_cascades_on_profile_delete(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "smart-desk.db")
    profile_id = "profile-" + "c" * 32
    await database.start()
    await database.write(
        lambda connection: (
            connection.execute(
                "INSERT INTO profiles (id, name, sitting_height_cm, standing_height_cm, led_color) "
                "VALUES (?, ?, ?, ?, ?)",
                (profile_id, "cascade", 80.0, 100.0, None),
            ),
            connection.execute(
                "INSERT INTO profile_modes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("mode-" + "c" * 32, profile_id, "독서", "독서", 80.0, 100.0,
                 None, None, None, None, None),
            ),
            connection.execute("DELETE FROM profiles WHERE id = ?", (profile_id,)),
        )
    )
    assert await database.read(
        lambda connection: connection.execute("SELECT COUNT(*) FROM profile_modes").fetchone()[0]
    ) == 0
    await database.stop()


async def test_version_four_data_migrates_to_version_nine_with_nullable_new_columns(
    tmp_path: Path,
) -> None:
    path = tmp_path / "smart-desk.db"
    profile = ("profile-" + "d" * 32, "기존 사용자", 80.0, 105.0, "FF3000")
    mode = ("mode-" + "d" * 32, profile[0], "독서", "독서", 82.0, 108.0, None)
    with sqlite3.connect(path, isolation_level=None) as connection:
        _migrate_to_version_1(connection)
        _migrate_to_version_2(connection)
        _migrate_to_version_3(connection)
        _migrate_to_version_4(connection)
        connection.execute("INSERT INTO profiles VALUES (?, ?, ?, ?, ?)", profile)
        connection.execute(
            "INSERT INTO profile_modes VALUES (?, ?, ?, ?, ?, ?, ?)", mode
        )

    database = SQLiteDatabase(path)
    await database.start()
    version, restored_profile, restored_mode = await database.read(
        lambda connection: (
            connection.execute("PRAGMA user_version").fetchone()[0],
            tuple(connection.execute("SELECT * FROM profiles").fetchone()),
            tuple(connection.execute("SELECT * FROM profile_modes").fetchone()),
        )
    )

    assert version == 9
    assert restored_profile == profile + (None, None, None, None, None)
    assert restored_mode == mode + (None, None, None, None)
    await database.stop()


async def test_version_five_data_migrates_to_version_nine_with_nullable_pin_hash(
    tmp_path: Path,
) -> None:
    path = tmp_path / "smart-desk.db"
    profile = ("profile-" + "f" * 32, "틸트 사용자", 80.0, 105.0, None)
    with sqlite3.connect(path, isolation_level=None) as connection:
        _migrate_to_version_1(connection)
        _migrate_to_version_2(connection)
        _migrate_to_version_3(connection)
        _migrate_to_version_4(connection)
        _migrate_to_version_5(connection)
        connection.execute(
            "INSERT INTO profiles (id, name, sitting_height_cm, standing_height_cm, led_color, "
            "tilt_level, description) VALUES (?, ?, ?, ?, ?, ?, ?)",
            profile + (4, "기존 틸트 설정"),
        )

    database = SQLiteDatabase(path)
    await database.start()
    version, restored = await database.read(
        lambda connection: (
            connection.execute("PRAGMA user_version").fetchone()[0],
            tuple(connection.execute("SELECT * FROM profiles").fetchone()),
        )
    )

    assert version == 9
    assert restored == profile + (4, "기존 틸트 설정", None, None, None)
    await database.stop()


async def test_tilt_level_out_of_range_is_rejected_by_check_constraint(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "smart-desk.db")
    await database.start()

    with pytest.raises(Exception):
        await database.write(
            lambda connection: connection.execute(
                "INSERT INTO profiles "
                "(id, name, sitting_height_cm, standing_height_cm, tilt_level) "
                "VALUES (?, ?, ?, ?, ?)",
                ("profile-" + "e" * 32, "out of range", 80.0, 100.0, 11),
            )
        )
    await database.stop()


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
