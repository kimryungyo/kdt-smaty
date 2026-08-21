import asyncio
from datetime import UTC, datetime, timedelta

from smart_desk.modules.identity.models import EnrollmentState, FaceEmbedding, SessionKind
from smart_desk.modules.identity.service import FaceIdentityService, FaceRecognizer
from smart_desk.modules.identity.session import CurrentUserSessionService
from smart_desk.modules.vision.models import (
    FaceBox,
    FreshFaceObservation,
    IdentityStatus,
    PresenceStatus,
)


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value


class Vision:
    def __init__(self) -> None:
        self.presence = PresenceStatus.PRESENT_SINGLE
        self.face: FreshFaceObservation | None = None

    def get_snapshot(self):
        return type("Snapshot", (), {"stable_presence": self.presence})()

    def get_fresh_face_observation(self):
        return self.face


class Extractor:
    model_name = "fake"
    model_version = "1"
    dimension = 2
    normalization = "l2"

    def extract(self, observation):
        return (1.0, 0.0)


class Repository:
    def __init__(self) -> None:
        sample = FaceEmbedding("fake", "1", 2, "l2", datetime(2026, 1, 1, tzinfo=UTC), (1.0, 0.0))
        self.samples = {"a": [sample, sample, sample]}

    async def load(self, **kwargs):
        return {profile: list(samples) for profile, samples in self.samples.items()}

    async def replace(self, profile_id, samples):
        self.samples[profile_id] = list(samples)

    async def delete(self, profile_id):
        return self.samples.pop(profile_id, None) is not None

    async def profile_exists(self, profile_id):
        return profile_id == "a"


class BlockingDeleteRepository(Repository):
    def __init__(self, *, fail: bool = False) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.fail = fail

    async def delete(self, profile_id):
        self.started.set()
        await self.release.wait()
        if self.fail:
            raise RuntimeError("storage unavailable")
        return await super().delete(profile_id)


class FutureDeleteRepository(Repository):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.pending: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

    async def delete(self, _profile_id):
        self.started.set()
        return await self.pending


class BlockingLoadRepository(Repository):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def load(self, **kwargs):
        self.started.set()
        await self.release.wait()
        return await super().load(**kwargs)


def face(capture: float) -> FreshFaceObservation:
    return FreshFaceObservation(
        frame=None,
        boxes=(FaceBox(0, 0, 10, 10),),
        captured_monotonic=capture,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=capture),
    )


async def test_enrollment_defers_adjacent_frames_until_sample_interval() -> None:
    vision = Vision()
    vision.face = face(1.0)
    service = FaceIdentityService(
        vision=vision, repository=Repository(), current_user=CurrentUserSessionService(),
        extractor=Extractor(), enrollment_sample_interval_seconds=0.5,
    )
    enrollment = await service.start_enrollment("a")
    await service.process_once()
    vision.face = face(1.25)
    await service.process_once()
    snapshot = await service.enrollment(enrollment.enrollment_id)
    assert snapshot is not None
    assert snapshot.state is EnrollmentState.CAPTURING
    assert snapshot.accepted_samples == 1


async def test_presence_starts_anonymous_immediately_then_distinct_match_replaces_it() -> None:
    clock = Clock()
    vision = Vision()
    sessions = CurrentUserSessionService(session_id_factory=iter(["anon", "a"]).__next__)
    service = FaceIdentityService(
        vision=vision,
        repository=Repository(),
        current_user=sessions,
        extractor=Extractor(),
        recognizer=FaceRecognizer(match_threshold=0.8, margin=0.1),
        monotonic=clock.monotonic,
    )

    await service.process_once()
    assert (await sessions.snapshot()).kind is SessionKind.ANONYMOUS

    vision.face = face(1.0)
    clock.value = 1.0
    await service.process_once()
    assert service.identity().status is IdentityStatus.UNKNOWN
    clock.value = 5.0
    await service.process_once()  # Same capture cannot complete the candidate.
    assert (await sessions.snapshot()).kind is SessionKind.ANONYMOUS

    vision.face = face(2.0)
    await service.process_once()
    current = await sessions.snapshot()
    assert current is not None and current.kind is SessionKind.REGISTERED
    assert current.profile_id == "a"
    assert service.identity().status is IdentityStatus.MATCHED
    assert service.identity().expires_at is not None


async def test_vacant_ends_but_no_face_and_multiple_preserve_session() -> None:
    vision = Vision()
    sessions = CurrentUserSessionService()
    service = FaceIdentityService(vision=vision, repository=Repository(), current_user=sessions)
    await sessions.select(SessionKind.REGISTERED, "a", "TEST")
    await service.process_once()
    assert (await sessions.snapshot()).profile_id == "a"
    vision.presence = PresenceStatus.MULTIPLE
    await service.process_once()
    assert (await sessions.snapshot()).profile_id == "a"
    vision.presence = PresenceStatus.VACANT
    await service.process_once()
    assert await sessions.snapshot() is None


async def test_brief_vacancy_does_not_end_the_current_session() -> None:
    clock = Clock()
    vision = Vision()
    sessions = CurrentUserSessionService()
    service = FaceIdentityService(
        vision=vision,
        repository=Repository(),
        current_user=sessions,
        monotonic=clock.monotonic,
        vacant_grace_seconds=30.0,
    )
    await sessions.select(SessionKind.REGISTERED, "a", "TEST")
    vision.presence = PresenceStatus.VACANT

    await service.process_once()
    clock.value = 29.9
    await service.process_once()
    assert (await sessions.snapshot()).profile_id == "a"

    vision.presence = PresenceStatus.PRESENT_SINGLE
    await service.process_once()
    vision.presence = PresenceStatus.VACANT
    clock.value = 100.0
    await service.process_once()
    clock.value = 130.0
    await service.process_once()
    assert await sessions.snapshot() is None


async def test_candidate_resets_on_no_face_and_same_capture_never_advances_it() -> None:
    clock = Clock()
    vision = Vision()
    sessions = CurrentUserSessionService(session_id_factory=iter(["anon", "a"]).__next__)
    service = FaceIdentityService(
        vision=vision,
        repository=Repository(),
        current_user=sessions,
        extractor=Extractor(),
        recognizer=FaceRecognizer(match_threshold=0.8, margin=0.1),
        monotonic=clock.monotonic,
    )
    vision.face = face(1.0)
    await service.process_once()
    # Resetting on NO_FACE starts a fresh candidate.  Reusing its initial capture
    # after time passes cannot confirm it.
    vision.face = None
    clock.value = 10.0
    await service.process_once()
    vision.face = face(1.0)
    await service.process_once()
    clock.value = 20.0
    await service.process_once()
    assert (await sessions.snapshot()).kind is SessionKind.ANONYMOUS
    vision.face = face(2.0)
    await service.process_once()
    assert (await sessions.snapshot()).kind is SessionKind.REGISTERED


async def test_repository_load_failure_finishes_processing_as_storage_error() -> None:
    class FailingRepository(Repository):
        async def load(self, **kwargs):
            raise RuntimeError("storage unavailable")

    vision = Vision()
    vision.face = face(1.0)
    service = FaceIdentityService(
        vision=vision,
        repository=FailingRepository(),
        current_user=CurrentUserSessionService(),
        extractor=Extractor(),
        pairwise_consistency_threshold=0.8,
        duplicate_threshold=0.8,
    )
    enrollment = await service.start_enrollment("a")
    await service.process_once()
    vision.face = face(2.0)
    await service.process_once()
    vision.face = face(3.0)
    await service.process_once()
    finished = await service.enrollment(enrollment.enrollment_id)
    assert finished is not None
    assert finished.state is EnrollmentState.FAILED
    assert finished.failure_code == "STORAGE_ERROR"


async def test_delete_suspends_processing_until_repository_delete_finishes() -> None:
    vision = Vision()
    vision.face = face(1.0)
    repository = BlockingDeleteRepository()
    sessions = CurrentUserSessionService()
    service = FaceIdentityService(
        vision=vision, repository=repository, current_user=sessions,
        extractor=Extractor(), recognizer=FaceRecognizer(match_threshold=0.8, margin=0.1),
    )
    deletion = asyncio.create_task(service.delete_face("a"))
    await repository.started.wait()

    await service.process_once()
    assert await sessions.snapshot() is None
    assert service.identity().status is IdentityStatus.UNKNOWN

    repository.release.set()
    assert await deletion is True


async def test_delete_failure_preserves_cache_and_processing_resumes() -> None:
    clock = Clock()
    vision = Vision()
    vision.face = face(1.0)
    repository = BlockingDeleteRepository(fail=True)
    sessions = CurrentUserSessionService(session_id_factory=iter(["anon", "anon-2", "a"]).__next__)
    service = FaceIdentityService(
        vision=vision, repository=repository, current_user=sessions, extractor=Extractor(),
        recognizer=FaceRecognizer(match_threshold=0.8, margin=0.1), monotonic=clock.monotonic,
    )
    # Prime the durable cache before the failed DELETE.
    await service.process_once()
    assert service._template_cache is not None  # cache atomicity evidence
    deletion = asyncio.create_task(service.delete_face("a"))
    await repository.started.wait()
    repository.release.set()
    try:
        await deletion
    except RuntimeError:
        pass
    else:
        raise AssertionError("delete should propagate repository failure")

    assert "a" in service._template_cache
    clock.value = 10.0
    vision.face = face(2.0)
    await service.process_once()
    clock.value = 14.0
    vision.face = face(3.0)
    await service.process_once()
    current = await sessions.snapshot()
    assert current is not None and current.kind is SessionKind.REGISTERED


async def test_cancelled_delete_releases_suspension_without_changing_cache() -> None:
    vision = Vision()
    vision.face = face(1.0)
    repository = FutureDeleteRepository()
    sessions = CurrentUserSessionService()
    service = FaceIdentityService(
        vision=vision, repository=repository, current_user=sessions,
        extractor=Extractor(), recognizer=FaceRecognizer(match_threshold=0.8, margin=0.1),
    )
    await service.process_once()
    assert service._template_cache is not None

    deletion = asyncio.create_task(service.delete_face("a"))
    await repository.started.wait()
    repository.pending.cancel()
    try:
        await deletion
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("cancelled repository future should cancel deletion")

    assert service._suspension is None
    assert "a" in service._template_cache


async def test_profile_delete_abort_allows_processing_to_resume() -> None:
    vision = Vision()
    vision.face = face(1.0)
    sessions = CurrentUserSessionService()
    service = FaceIdentityService(
        vision=vision, repository=Repository(), current_user=sessions,
        extractor=Extractor(), recognizer=FaceRecognizer(match_threshold=0.8, margin=0.1),
    )
    await service.prepare_profile_delete("a")
    await service.process_once()
    assert await sessions.snapshot() is None
    assert service.identity().status is IdentityStatus.UNKNOWN

    await service.abort_profile_delete("a")
    await service.process_once()
    current = await sessions.snapshot()
    assert current is not None and current.kind is SessionKind.ANONYMOUS


async def test_stop_clears_suspension_so_restart_processes_faces() -> None:
    vision = Vision()
    vision.face = face(1.0)
    sessions = CurrentUserSessionService()
    service = FaceIdentityService(
        vision=vision, repository=Repository(), current_user=sessions,
        extractor=Extractor(), recognizer=FaceRecognizer(match_threshold=0.8, margin=0.1),
    )
    await service.start()
    await service.prepare_profile_delete("a")
    await service.stop()

    await service.start()
    await service.process_once()
    current = await sessions.snapshot()
    assert service._suspension is None
    assert current is not None and current.kind is SessionKind.ANONYMOUS
    await service.stop()


async def test_stale_inference_cannot_restore_candidate_after_enrollment_starts() -> None:
    vision = Vision()
    vision.face = face(1.0)
    repository = BlockingLoadRepository()
    service = FaceIdentityService(
        vision=vision, repository=repository, current_user=CurrentUserSessionService(),
        extractor=Extractor(), recognizer=FaceRecognizer(match_threshold=0.8, margin=0.1),
    )
    processing = asyncio.create_task(service.process_once())
    await repository.started.wait()
    enrollment = await service.start_enrollment("a")
    repository.release.set()
    await processing

    assert service._candidate is None  # stale inference may not recreate state
    current = await service.enrollment(enrollment.enrollment_id)
    assert current is not None and current.state is EnrollmentState.WAITING_FACE


async def test_mutation_starts_reset_public_identity_immediately() -> None:
    vision = Vision()
    vision.face = face(1.0)
    repository = BlockingDeleteRepository()
    service = FaceIdentityService(
        vision=vision, repository=repository, current_user=CurrentUserSessionService(),
        extractor=Extractor(), recognizer=FaceRecognizer(match_threshold=0.8, margin=0.1),
        candidate_seconds=0,
    )
    await service.process_once()
    vision.face = face(2.0)
    await service.process_once()
    assert service.identity().status is IdentityStatus.MATCHED

    enrollment = await service.start_enrollment("a")
    assert service.identity().status is IdentityStatus.UNKNOWN
    await service.cancel(enrollment.enrollment_id)
    vision.face = face(3.0)
    await service.process_once()
    vision.face = face(4.0)
    await service.process_once()
    assert service.identity().status is IdentityStatus.MATCHED
    deletion = asyncio.create_task(service.delete_face("a"))
    await repository.started.wait()
    assert service.identity().status is IdentityStatus.UNKNOWN
    repository.release.set()
    await deletion
