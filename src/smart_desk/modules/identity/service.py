"""Fail-closed face identity, enrollment, and session decision boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import math
import threading
import time
from typing import Protocol
from uuid import uuid4

from smart_desk.modules.identity.models import (
    EnrollmentSnapshot,
    EnrollmentState,
    FaceEmbedding,
    IdentityObservation,
    SessionKind,
)
from smart_desk.modules.identity.repository import FaceEmbeddingRepository
from smart_desk.modules.identity.session import CurrentUserSessionService
from smart_desk.modules.vision.models import FreshFaceObservation, IdentityStatus, PresenceStatus


class ModelUnavailableError(RuntimeError):
    pass


class FreshSingleFaceRequiredError(RuntimeError):
    """Enrollment has no current camera/Vision single-face input."""


class EnrollmentConflictError(RuntimeError):
    """Enrollment is already active or is not in a cancellable state."""


class FaceEmbeddingExtractor(Protocol):
    model_name: str
    model_version: str
    dimension: int
    normalization: str

    def extract(self, observation: FreshFaceObservation) -> tuple[float, ...] | None:
        ...


class UnavailableFaceEmbeddingExtractor:
    model_name = "unavailable"
    model_version = "unavailable"
    dimension = 0
    normalization = "unavailable"

    def extract(self, observation: FreshFaceObservation) -> tuple[float, ...] | None:
        raise ModelUnavailableError("MODEL_UNAVAILABLE")


class FaceRecognizer:
    """Open-set recognizer that intentionally never exposes raw scores."""

    def __init__(self, *, match_threshold: float | None = None, margin: float | None = None) -> None:
        self._threshold = match_threshold
        self._margin = margin

    def recognize(
        self,
        vector: tuple[float, ...],
        samples: dict[str, list[FaceEmbedding]],
    ) -> IdentityStatus | tuple[IdentityStatus, str]:
        if self._threshold is None or self._margin is None:
            return IdentityStatus.UNKNOWN
        if not _valid_vector(vector):
            return IdentityStatus.UNKNOWN
        scores: list[tuple[float, str]] = []
        for profile_id, items in samples.items():
            compatible = [item.vector for item in items if _valid_vector(item.vector, len(vector))]
            if compatible:
                scores.append((max(_cosine(vector, item) for item in compatible), profile_id))
        scores.sort()
        if not scores or scores[-1][0] < self._threshold:
            return IdentityStatus.UNKNOWN_FACE
        if len(scores) > 1 and scores[-1][0] - scores[-2][0] < self._margin:
            return IdentityStatus.AMBIGUOUS
        return IdentityStatus.MATCHED, scores[-1][1]


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not _valid_vector(left) or not _valid_vector(right, len(left)):
        return -1.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    denominator = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return numerator / denominator if denominator else -1.0


def _valid_vector(vector: tuple[float, ...], dimension: int | None = None) -> bool:
    return bool(vector) and (dimension is None or len(vector) == dimension) and all(
        isinstance(value, (int, float)) and math.isfinite(value) for value in vector
    ) and any(value != 0.0 for value in vector)


class FaceIdentityService:
    """Serializes identity lifecycle state while extraction/SQLite work happens outside it."""

    def __init__(
        self,
        *,
        vision: object,
        repository: FaceEmbeddingRepository,
        current_user: CurrentUserSessionService,
        extractor: FaceEmbeddingExtractor | None = None,
        recognizer: FaceRecognizer | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
        candidate_seconds: float = 3.0,
        freshness_seconds: float = 2.0,
        pairwise_consistency_threshold: float | None = None,
        duplicate_threshold: float | None = None,
        enrollment_sample_interval_seconds: float = 0.0,
        vacant_grace_seconds: float = 0.0,
    ) -> None:
        self._vision = vision
        self._repo = repository
        self._current = current_user
        self._extractor = extractor or UnavailableFaceEmbeddingExtractor()
        self._recognizer = recognizer or FaceRecognizer()
        self._monotonic = monotonic
        self._utc_now = utc_now
        self._candidate_seconds = candidate_seconds
        self._freshness_seconds = freshness_seconds
        self._pairwise_consistency_threshold = pairwise_consistency_threshold
        self._duplicate_threshold = duplicate_threshold
        self._enrollment_sample_interval_seconds = enrollment_sample_interval_seconds
        self._vacant_grace_seconds = vacant_grace_seconds
        # VACANT가 처음 연속으로 관측되기 시작한 시각. 사람이 다시 보이면 지운다.
        self._vacant_since_mono: float | None = None
        self._state_lock = asyncio.Lock()
        self._model_lock = threading.Lock()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._identity = self._unknown_identity()
        self._identity_capture: float | None = None
        self._candidate: tuple[SessionKind, str | None, float, float] | None = None
        self._generation = 0
        self._enrollment: EnrollmentSnapshot | None = None
        self._samples: list[FaceEmbedding] = []
        self._last_capture: float | None = None
        self._template_cache: dict[str, list[FaceEmbedding]] | None = None
        self._suspension: tuple[str, int] | None = None

    async def start(self) -> None:
        async with self._state_lock:
            if self._task is not None and not self._task.done():
                return
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="face-identity")

    async def stop(self) -> None:
        async with self._state_lock:
            self._stop.set()
            task = self._task
            self._task = None
            self._invalidate_locked()
            self._enrollment = None
            # A deletion may be awaiting storage while the service stops.  Its
            # token is no longer relevant to this lifecycle, so do not let it
            # suspend recognition after a subsequent start.
            self._suspension = None
            self._identity = self._unknown_identity()
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._current.end("IDENTITY_STOPPED")

    def identity(self) -> IdentityObservation:
        return self._identity

    async def start_enrollment(self, profile_id: str) -> EnrollmentSnapshot:
        if not self._extractor_available():
            raise ModelUnavailableError("MODEL_UNAVAILABLE")
        observation = self._vision.get_fresh_face_observation()
        if observation is None or len(observation.boxes) != 1:
            raise FreshSingleFaceRequiredError("FRESH_SINGLE_FACE_REQUIRED")
        # The database check is deliberately before allocating the exclusive state.
        # It avoids a phantom enrollment for a deleted/nonexistent profile.
        if not await self._profile_exists(profile_id):
            from smart_desk.modules.profiles.repository import ProfileNotFoundError
            raise ProfileNotFoundError("요청한 프로필을 찾을 수 없습니다.")
        async with self._state_lock:
            if self._suspension is not None or self._active_enrollment_locked():
                raise EnrollmentConflictError("ENROLLMENT_IN_PROGRESS")
            self._invalidate_locked()
            now = self._utc_now()
            self._enrollment = EnrollmentSnapshot(
                enrollment_id=f"enrollment-{uuid4().hex}",
                profile_id=profile_id,
                state=EnrollmentState.WAITING_FACE,
                required_samples=3,
                accepted_samples=0,
                started_at=now,
                changed_at=now,
            )
            result = self._enrollment
        await self._current.end("FACE_ENROLLMENT_STARTED")
        return result

    async def enrollment(self, enrollment_id: str) -> EnrollmentSnapshot | None:
        async with self._state_lock:
            if self._enrollment and self._enrollment.enrollment_id == enrollment_id:
                return self._enrollment
            return None

    async def cancel(self, enrollment_id: str) -> bool:
        async with self._state_lock:
            snapshot = self._enrollment
            if snapshot is None or snapshot.enrollment_id != enrollment_id:
                return False
            if snapshot.state not in {
                EnrollmentState.WAITING_FACE,
                EnrollmentState.CAPTURING,
            }:
                raise EnrollmentConflictError("ENROLLMENT_NOT_CANCELLABLE")
            self._invalidate_locked()
            self._enrollment = snapshot.model_copy(
                update={"state": EnrollmentState.CANCELLED, "changed_at": self._utc_now()}
            )
            return True

    async def delete_face(self, profile_id: str) -> bool:
        async with self._state_lock:
            token = self._begin_suspension_locked(profile_id)
        committed = False
        deleted = False
        try:
            await self._current.end("FACE_DELETION_STARTED")
            deleted = await self._repo.delete(profile_id)
            committed = True
        finally:
            async with self._state_lock:
                # The repository is the commit boundary.  A cancellation or
                # failure before it returns must leave this snapshot untouched.
                if committed and deleted and self._template_cache is not None:
                    cache = dict(self._template_cache)
                    cache.pop(profile_id, None)
                    self._template_cache = cache
                self._release_suspension_locked(profile_id, token)
        return deleted

    async def prepare_profile_delete(self, profile_id: str) -> None:
        """Invalidate in-memory identity before the profile repository commits DELETE."""
        async with self._state_lock:
            self._begin_suspension_locked(profile_id)
        await self._current.end("PROFILE_DELETION_STARTED")

    async def finalize_profile_delete(self, profile_id: str) -> None:
        """Remove only the committed profile from the immutable template snapshot."""
        async with self._state_lock:
            suspension = self._suspension
            if suspension is None or suspension[0] != profile_id:
                return
            if self._template_cache is not None:
                cache = dict(self._template_cache)
                cache.pop(profile_id, None)
                self._template_cache = cache
            self._release_suspension_locked(profile_id, suspension[1])

    async def abort_profile_delete(self, profile_id: str) -> None:
        """Resume recognition when the profile DELETE did not commit."""
        async with self._state_lock:
            suspension = self._suspension
            if suspension is not None and suspension[0] == profile_id:
                self._release_suspension_locked(profile_id, suspension[1])

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.process_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                async with self._state_lock:
                    self._identity = self._unknown_identity()
                    self._candidate = None
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=0.2)
            except TimeoutError:
                pass

    async def process_once(self) -> None:
        snapshot = self._vision.get_snapshot()
        observation = self._vision.get_fresh_face_observation()
        async with self._state_lock:
            capturing = self._suspension is None and self._active_enrollment_locked()
            suspended = self._suspension is not None
        if suspended:
            return
        if capturing:
            await self._capture(observation)
            return

        async with self._state_lock:
            generation = self._generation
        raw_identity, captured = await self._infer(observation)
        identity = await self._advance_session(
            snapshot.stable_presence, raw_identity, captured, generation
        )
        async with self._state_lock:
            if generation != self._generation:
                return
            self._identity = identity
            self._identity_capture = captured

    async def _infer(
        self, observation: FreshFaceObservation | None
    ) -> tuple[IdentityObservation, float | None]:
        if observation is None:
            return self._observation(IdentityStatus.NO_FACE, None, None), None
        if len(observation.boxes) != 1:
            return self._observation(IdentityStatus.UNKNOWN, None, observation.observed_at), observation.captured_monotonic
        async with self._state_lock:
            generation = self._generation
        try:
            vector = await asyncio.to_thread(self._extract, observation)
        except ModelUnavailableError:
            return self._observation(IdentityStatus.UNKNOWN, None, observation.observed_at), observation.captured_monotonic
        except Exception:
            return self._observation(IdentityStatus.UNKNOWN, None, observation.observed_at), observation.captured_monotonic
        if vector is None:
            return self._observation(IdentityStatus.UNKNOWN, None, observation.observed_at), observation.captured_monotonic
        async with self._state_lock:
            if generation != self._generation:
                return self._observation(IdentityStatus.UNKNOWN, None, observation.observed_at), observation.captured_monotonic
            cache = self._template_cache
        try:
            samples = cache if cache is not None else await self._repo.load(
                model_name=self._extractor.model_name,
                model_version=self._extractor.model_version,
                dimension=self._extractor.dimension,
                normalization=self._extractor.normalization,
            )
        except Exception:
            return self._observation(IdentityStatus.UNKNOWN, None, observation.observed_at), observation.captured_monotonic
        async with self._state_lock:
            if generation != self._generation:
                return self._observation(IdentityStatus.UNKNOWN, None, observation.observed_at), observation.captured_monotonic
            if self._template_cache is None:
                self._template_cache = {key: list(value) for key, value in samples.items()}
        result = self._recognizer.recognize(vector, samples)
        status, profile_id = result if isinstance(result, tuple) else (result, None)
        return self._observation(status, profile_id, observation.observed_at), observation.captured_monotonic

    def _extract(self, observation: FreshFaceObservation) -> tuple[float, ...] | None:
        with self._model_lock:
            vector = self._extractor.extract(observation)
        if vector is None or len(vector) != self._extractor.dimension:
            return None
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector):
            return None
        return tuple(float(value) for value in vector)

    async def _advance_session(
        self,
        presence: PresenceStatus,
        identity: IdentityObservation,
        captured: float | None,
        generation: int,
    ) -> IdentityObservation:
        if presence is PresenceStatus.VACANT:
            async with self._state_lock:
                if generation != self._generation or self._suspension is not None:
                    return self._unknown_identity()
                self._candidate = None
                # VACANT 한 번에 곧바로 session을 끝내면, 몸을 잠깐 기울이거나
                # 검출이 흔들릴 때마다 session이 새로 발급되고 진행 중이던 자동
                # 이동이 취소된다. 계속 비어 있는 것이 확인될 때만 끝낸다.
                now = self._monotonic()
                if self._vacant_since_mono is None:
                    self._vacant_since_mono = now
                waited = now - self._vacant_since_mono
            if waited < self._vacant_grace_seconds:
                return self._observation(
                    IdentityStatus.UNKNOWN, None, identity.observed_at
                )
            current = await self._current.snapshot()
            if current is not None:
                await self._current.end_if_current(current.session_id, "VACANT")
            return self._observation(IdentityStatus.UNKNOWN, None, identity.observed_at)
        # 사람이 보이면 부재 시계를 지운다. 잠깐의 VACANT는 누적되지 않는다.
        async with self._state_lock:
            self._vacant_since_mono = None
        if presence is not PresenceStatus.PRESENT_SINGLE:
            async with self._state_lock:
                if generation != self._generation or self._suspension is not None:
                    return self._unknown_identity()
                self._candidate = None
            return self._observation(IdentityStatus.UNKNOWN, None, identity.observed_at)
        current = await self._current.snapshot()
        target = self._target_for(identity)
        if current is None:
            # Vision's stable presence already accounts for the 3-second presence hold.
            # A raw face result never postpones this anonymous session; identity has
            # its own independent confirmation timer.
            async with self._state_lock:
                if generation != self._generation or self._suspension is not None:
                    return self._unknown_identity()
            current = await self._current.select(SessionKind.ANONYMOUS, None, "PRESENCE_STABLE")
            async with self._state_lock:
                stale = generation != self._generation or self._suspension is not None
            if stale:
                await self._current.end_if_current(current.session_id, "IDENTITY_STALE")
                return self._unknown_identity()
        if target is None:
            async with self._state_lock:
                if generation != self._generation or self._suspension is not None:
                    return self._unknown_identity()
                self._candidate = None
            return self._observation(identity.status, None, identity.observed_at)
        if (
            current is not None
            and (current.kind, current.profile_id) == target
            and identity.status is not IdentityStatus.UNKNOWN_FACE
        ):
            async with self._state_lock:
                if generation != self._generation or self._suspension is not None:
                    return self._unknown_identity()
                self._candidate = None
            return self._observation(identity.status, current.profile_id, identity.observed_at)
        if captured is None:
            return self._observation(IdentityStatus.UNKNOWN, None, identity.observed_at)
        now = self._monotonic()
        async with self._state_lock:
            if generation != self._generation or self._suspension is not None:
                return self._unknown_identity()
            candidate = self._candidate
            if candidate is None or candidate[:2] != target:
                self._candidate = (target[0], target[1], now, captured)
                return self._observation(IdentityStatus.UNKNOWN, None, identity.observed_at)
            if captured <= candidate[3]:
                return self._observation(IdentityStatus.UNKNOWN, None, identity.observed_at)
            self._candidate = (candidate[0], candidate[1], candidate[2], captured)
            if now - candidate[2] < self._candidate_seconds:
                return self._observation(IdentityStatus.UNKNOWN, None, identity.observed_at)
            self._candidate = None
        async with self._state_lock:
            if generation != self._generation or self._suspension is not None:
                return self._unknown_identity()
        selected = await self._current.select(*target, "IDENTITY_STABLE")
        async with self._state_lock:
            stale = generation != self._generation or self._suspension is not None
        if stale:
            await self._current.end_if_current(selected.session_id, "IDENTITY_STALE")
            return self._unknown_identity()
        return self._observation(identity.status, target[1], identity.observed_at)

    def _target_for(self, identity: IdentityObservation) -> tuple[SessionKind, str | None] | None:
        if identity.status is IdentityStatus.MATCHED and identity.profile_id:
            return SessionKind.REGISTERED, identity.profile_id
        if identity.status is IdentityStatus.UNKNOWN_FACE:
            return SessionKind.ANONYMOUS, None
        return None

    async def _capture(self, observation: FreshFaceObservation | None) -> None:
        if observation is None or len(observation.boxes) != 1:
            return
        async with self._state_lock:
            snapshot = self._enrollment
            generation = self._generation
            if snapshot is None or not self._active_enrollment_locked():
                return
            if self._last_capture is not None and (
                observation.captured_monotonic <= self._last_capture
                or observation.captured_monotonic - self._last_capture < self._enrollment_sample_interval_seconds
            ):
                return
            self._enrollment = snapshot.model_copy(
                update={"state": EnrollmentState.CAPTURING, "changed_at": self._utc_now()}
            )
        try:
            vector = await asyncio.to_thread(self._extract, observation)
        except ModelUnavailableError:
            await self._fail_capture(generation, "MODEL_UNAVAILABLE")
            return
        except Exception:
            await self._fail_capture(generation, "MODEL_ERROR")
            return
        if vector is None:
            await self._fail_capture(generation, "LOW_QUALITY")
            return
        item = FaceEmbedding(
            self._extractor.model_name, self._extractor.model_version,
            self._extractor.dimension, self._extractor.normalization,
            observation.observed_at, vector,
        )
        async with self._state_lock:
            if generation != self._generation or self._enrollment is None:
                return
            self._samples.append(item)
            self._last_capture = observation.captured_monotonic
            self._enrollment = self._enrollment.model_copy(
                update={"accepted_samples": len(self._samples), "changed_at": self._utc_now()}
            )
            if len(self._samples) < 3:
                return
            enrollment = self._enrollment.model_copy(
                update={"state": EnrollmentState.PROCESSING, "changed_at": self._utc_now()}
            )
            self._enrollment = enrollment
            samples = list(self._samples)
        if not self._samples_consistent(samples):
            await self._fail_capture(generation, "INCONSISTENT_SAMPLES")
            return
        try:
            duplicate, loaded_cache = await self._duplicates_other_profile(
                enrollment.profile_id, samples
            )
        except Exception:
            await self._fail_capture(generation, "STORAGE_ERROR")
            return
        if duplicate:
            await self._fail_capture(generation, "DUPLICATE_FACE")
            return
        try:
            await self._repo.replace(enrollment.profile_id, samples)
        except Exception:
            await self._fail_capture(generation, "STORAGE_ERROR")
            return
        async with self._state_lock:
            if generation != self._generation or self._enrollment is None:
                return
            cache = dict(loaded_cache)
            cache[enrollment.profile_id] = list(samples)
            self._template_cache = cache
            self._samples.clear()
            self._enrollment = self._enrollment.model_copy(
                update={"state": EnrollmentState.SUCCEEDED, "changed_at": self._utc_now()}
            )

    async def _fail_capture(self, generation: int, failure_code: str) -> None:
        async with self._state_lock:
            if generation != self._generation or self._enrollment is None:
                return
            self._samples.clear()
            self._enrollment = self._enrollment.model_copy(
                update={"state": EnrollmentState.FAILED, "failure_code": failure_code, "changed_at": self._utc_now()}
            )

    def _extractor_available(self) -> bool:
        return (
            self._extractor.dimension > 0
            and bool(self._extractor.model_name.strip())
            and bool(self._extractor.model_version.strip())
            and bool(self._extractor.normalization.strip())
        )

    async def _profile_exists(self, profile_id: str) -> bool:
        return await self._repo.profile_exists(profile_id)

    def _active_enrollment_locked(self) -> bool:
        return self._enrollment is not None and self._enrollment.state in {
            EnrollmentState.WAITING_FACE,
            EnrollmentState.CAPTURING,
            EnrollmentState.PROCESSING,
        }

    def _invalidate_locked(self) -> None:
        self._generation += 1
        self._candidate = None
        self._samples.clear()
        self._last_capture = None
        self._identity = self._unknown_identity()
        self._identity_capture = None
        # Templates are durable state.  Generation invalidation must not discard
        # unrelated committed profiles after a cancelled operation.

    def _begin_suspension_locked(self, profile_id: str) -> int:
        if self._suspension is not None:
            raise EnrollmentConflictError("IDENTITY_MUTATION_IN_PROGRESS")
        self._invalidate_locked()
        if self._enrollment and self._enrollment.profile_id == profile_id and self._active_enrollment_locked():
            self._enrollment = self._enrollment.model_copy(
                update={"state": EnrollmentState.CANCELLED, "changed_at": self._utc_now()}
            )
        token = self._generation
        self._suspension = (profile_id, token)
        return token

    def _release_suspension_locked(self, profile_id: str, token: int) -> None:
        if self._suspension == (profile_id, token):
            self._suspension = None

    def _unknown_identity(self) -> IdentityObservation:
        return IdentityObservation(IdentityStatus.UNKNOWN, None, None, None)

    def _observation(
        self, status: IdentityStatus, profile_id: str | None, observed_at: datetime | None
    ) -> IdentityObservation:
        expires_at = observed_at + timedelta(seconds=self._freshness_seconds) if observed_at else None
        return IdentityObservation(status, profile_id, observed_at, expires_at)

    def _samples_consistent(self, samples: list[FaceEmbedding]) -> bool:
        if self._pairwise_consistency_threshold is None:
            return False
        return all(
            _cosine(left.vector, right.vector) >= self._pairwise_consistency_threshold
            for index, left in enumerate(samples)
            for right in samples[index + 1 :]
        )

    async def _duplicates_other_profile(
        self, profile_id: str, samples: list[FaceEmbedding]
    ) -> tuple[bool, dict[str, list[FaceEmbedding]]]:
        if self._duplicate_threshold is None:
            return False, dict(self._template_cache or {})
        async with self._state_lock:
            cache = self._template_cache
        if cache is None:
            cache = await self._repo.load(
                model_name=self._extractor.model_name,
                model_version=self._extractor.model_version,
                dimension=self._extractor.dimension,
                normalization=self._extractor.normalization,
            )
        duplicate = any(
            _cosine(sample.vector, other.vector) >= self._duplicate_threshold
            for candidate_profile, values in cache.items()
            if candidate_profile != profile_id
            for sample in samples
            for other in values
        )
        return duplicate, {key: list(value) for key, value in cache.items()}
