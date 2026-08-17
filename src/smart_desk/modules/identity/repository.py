from __future__ import annotations

from datetime import UTC, datetime
import math
import struct
from sqlite3 import Connection, Row

from smart_desk.modules.identity.models import FaceEmbedding
from smart_desk.storage import SQLiteDatabase


class FaceEmbeddingRepositoryError(RuntimeError):
    """Invalid embedding data or an impossible repository request."""


class FaceEmbeddingRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    async def replace(self, profile_id: str, embeddings: list[FaceEmbedding]) -> None:
        _validate_set(embeddings)

        def operation(conn: Connection) -> None:
            if conn.execute("SELECT 1 FROM profiles WHERE id = ?", (profile_id,)).fetchone() is None:
                raise FaceEmbeddingRepositoryError("요청한 프로필을 찾을 수 없습니다.")
            conn.execute("DELETE FROM face_embeddings WHERE profile_id = ?", (profile_id,))
            for index, item in enumerate(embeddings):
                conn.execute(
                    "INSERT INTO face_embeddings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        profile_id, index, item.model_name, item.model_version,
                        item.dimension, item.normalization, _utc(item.created_at),
                        _pack(item.vector),
                    ),
                )
        await self._database.write(operation)

    async def delete(self, profile_id: str) -> bool:
        return await self._database.write(
            lambda connection: connection.execute(
                "DELETE FROM face_embeddings WHERE profile_id = ?", (profile_id,)
            ).rowcount > 0
        )

    async def profile_exists(self, profile_id: str) -> bool:
        return await self._database.read(
            lambda connection: connection.execute(
                "SELECT 1 FROM profiles WHERE id = ?", (profile_id,)
            ).fetchone() is not None
        )

    async def load(
        self, *, model_name: str, model_version: str, dimension: int, normalization: str
    ) -> dict[str, list[FaceEmbedding]]:
        def operation(conn: Connection) -> dict[str, list[FaceEmbedding]]:
            rows = conn.execute(
                "SELECT * FROM face_embeddings WHERE model_name=? AND model_version=? "
                "AND dimension=? AND normalization=? ORDER BY profile_id,sample_index",
                (model_name, model_version, dimension, normalization),
            ).fetchall()
            result: dict[str, list[FaceEmbedding]] = {}
            for row in rows:
                try:
                    item = _from_row(row)
                except (FaceEmbeddingRepositoryError, ValueError, TypeError, struct.error):
                    continue
                result.setdefault(row["profile_id"], []).append(item)
            valid: dict[str, list[FaceEmbedding]] = {}
            for profile_id, items in result.items():
                try:
                    _validate_set(items)
                except FaceEmbeddingRepositoryError:
                    continue
                valid[profile_id] = items
            return valid
        return await self._database.read(operation)


def _validate_set(items: list[FaceEmbedding]) -> None:
    if not 3 <= len(items) <= 5:
        raise FaceEmbeddingRepositoryError("얼굴 표본은 3~5개여야 합니다.")
    meta = _validate_item(items[0])
    for item in items:
        if _validate_item(item) != meta:
            raise FaceEmbeddingRepositoryError("유효하지 않은 얼굴 표본입니다.")


def _validate_item(item: FaceEmbedding) -> tuple[str, str, int, str]:
    if (
        not item.model_name.strip()
        or not item.model_version.strip()
        or not item.normalization.strip()
        or item.dimension <= 0
        or item.dimension != len(item.vector)
        or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in item.vector)
        or item.created_at.tzinfo is None
        or item.created_at.utcoffset() != UTC.utcoffset(item.created_at)
    ):
        raise FaceEmbeddingRepositoryError("유효하지 않은 얼굴 표본입니다.")
    try:
        _pack(item.vector)
    except (OverflowError, struct.error) as error:
        raise FaceEmbeddingRepositoryError("float32로 저장할 수 없는 얼굴 표본입니다.") from error
    return (item.model_name, item.model_version, item.dimension, item.normalization)


def _pack(vector: tuple[float, ...]) -> bytes:
    return struct.pack("<" + "f" * len(vector), *vector)


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _from_row(row: Row) -> FaceEmbedding:
    vector = row["vector"]
    dimension = row["dimension"]
    if not isinstance(dimension, int) or dimension <= 0:
        raise FaceEmbeddingRepositoryError("손상된 얼굴 표본입니다.")
    if not isinstance(vector, bytes) or len(vector) != dimension * 4:
        raise FaceEmbeddingRepositoryError("손상된 얼굴 표본입니다.")
    values = struct.unpack("<" + "f" * dimension, vector)
    if not all(math.isfinite(value) for value in values):
        raise FaceEmbeddingRepositoryError("손상된 얼굴 표본입니다.")
    created_at = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
    item = FaceEmbedding(
        row["model_name"], row["model_version"], dimension, row["normalization"], created_at, values
    )
    _validate_item(item)
    return item
