from datetime import UTC, datetime
import pytest

from smart_desk.modules.identity.models import FaceEmbedding
from smart_desk.modules.identity.repository import FaceEmbeddingRepository, FaceEmbeddingRepositoryError
from smart_desk.storage import SQLiteDatabase


def sample(value: float) -> FaceEmbedding:
    return FaceEmbedding("fake", "1", 2, "l2", datetime.now(UTC), (value, 0.0))


async def test_embedding_replace_round_trips_and_profile_delete_cascades(tmp_path) -> None:
    database = SQLiteDatabase(tmp_path / "db.sqlite")
    await database.start()
    await database.write(lambda c: c.execute(
        "INSERT INTO profiles (id, name, sitting_height_cm, standing_height_cm, led_color) "
        "VALUES ('p', 'P', 80, 100, NULL)"
    ))
    repository = FaceEmbeddingRepository(database)
    await repository.replace("p", [sample(1.0), sample(.9), sample(.8)])
    loaded = await repository.load(model_name="fake", model_version="1", dimension=2, normalization="l2")
    assert len(loaded["p"]) == 3
    assert loaded["p"][0].vector == pytest.approx((1.0, 0.0))
    await database.write(lambda c: c.execute("DELETE FROM profiles WHERE id='p'"))
    assert not await repository.load(model_name="fake", model_version="1", dimension=2, normalization="l2")
    await database.stop()


async def test_embedding_set_requires_three_to_five_finite_vectors(tmp_path) -> None:
    database = SQLiteDatabase(tmp_path / "db.sqlite")
    await database.start()
    repository = FaceEmbeddingRepository(database)
    with pytest.raises(FaceEmbeddingRepositoryError):
        await repository.replace("p", [sample(1.0), sample(.9)])
    with pytest.raises(FaceEmbeddingRepositoryError):
        await repository.replace("p", [sample(1.0)] * 6)
    await database.stop()
