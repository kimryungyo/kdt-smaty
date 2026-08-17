"""두 RTSP 최신 frame을 결합하는 fail-closed Vision service."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import threading
import time
from typing import Callable

import numpy as np

from smart_desk.config.settings import VisionSettings
from smart_desk.modules.media.frame_source import LatestFrame, RtspFrameSource
from smart_desk.modules.vision.detector import VisionDetector
from smart_desk.modules.vision.models import (
    AssociationResponse,
    BlockCode,
    CameraObservation,
    CameraStatus,
    CameraStatusResponse,
    FreshFaceObservation,
    IdentityResponse,
    LowerDetection,
    PostureResponse,
    PostureStatus,
    PresenceResponse,
    PresenceStatus,
    UpperDetection,
    VisionSnapshot,
    VisionStatusResponse,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class VisionService:
    """source별 최신 frame만 추론하고 불변 snapshot으로 결합한다."""

    def __init__(
        self,
        *,
        upper_source: RtspFrameSource | None,
        lower_source: RtspFrameSource | None,
        detector: VisionDetector,
        settings: VisionSettings,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._upper_source = upper_source
        self._lower_source = lower_source
        self._detector = detector
        self._settings = settings
        self._monotonic = monotonic
        self._utc_now = utc_now
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        # 동일 model 인스턴스의 upper/lower 호출 동시 안전성을 가정하지 않는다.
        self._detector_lock = threading.Lock()
        self._last_upper_capture: float | None = None
        self._last_lower_capture: float | None = None
        self._last_lower_inference_monotonic: float | None = None
        self._last_combined_pair: tuple[float, float] | None = None
        self._upper = self._empty_observation(upper_source)
        self._lower = self._empty_observation(lower_source)
        self._presence_candidate: tuple[PresenceStatus, float, datetime] | None = None
        self._posture_candidate: tuple[PostureStatus, float, datetime] | None = None
        self._stable_presence = PresenceStatus.UNKNOWN
        self._stable_posture = PostureStatus.UNKNOWN
        self._snapshot = self._compose_snapshot()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="vision-observation")

    async def stop(self) -> None:
        self._stop_event.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._stable_presence = PresenceStatus.UNKNOWN
        self._stable_posture = PostureStatus.UNKNOWN
        self._presence_candidate = None
        self._posture_candidate = None
        self._last_combined_pair = None
        self._snapshot = self._compose_snapshot(force_stale=True)

    async def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                await self.process_once()
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), self._settings.poll_interval_seconds
                    )
                except TimeoutError:
                    pass
        finally:
            # Cancel/start races and unexpected background termination must not leave
            # the last PRESENT result usable.
            self._stable_presence = PresenceStatus.UNKNOWN
            self._stable_posture = PostureStatus.UNKNOWN
            self._snapshot = self._compose_snapshot(force_stale=True)

    async def process_once(self) -> None:
        """새로운 distinct frame만 executor에서 추론한다. 테스트도 이 단위를 사용한다."""

        upper_frame = self._new_frame(self._upper_source, self._last_upper_capture)
        lower_frame = self._new_frame(self._lower_source, self._last_lower_capture)
        jobs: list[asyncio.Future[object] | asyncio.Task[object]] = []
        kinds: list[tuple[str, LatestFrame]] = []
        if upper_frame is not None:
            kinds.append(("upper", upper_frame))
            jobs.append(asyncio.to_thread(self._detect_upper, upper_frame[0]))
        if lower_frame is not None and self._lower_inference_due():
            kinds.append(("lower", lower_frame))
            jobs.append(asyncio.to_thread(self._detect_lower, lower_frame[0]))
        if jobs:
            results = await asyncio.gather(*jobs, return_exceptions=True)
            for (kind, frame), result in zip(kinds, results, strict=True):
                if kind == "upper":
                    self._last_upper_capture = frame[1]
                    self._upper = self._make_upper_observation(frame, result)
                else:
                    self._last_lower_capture = frame[1]
                    self._last_lower_inference_monotonic = self._monotonic()
                    self._lower = self._make_lower_observation(frame, result)
        self._snapshot = self._compose_snapshot(advance=self._has_distinct_combined_pair())

    def _detect_upper(self, frame: np.ndarray) -> object:
        with self._detector_lock:
            return self._detector.detect_upper(frame)

    def _detect_lower(self, frame: np.ndarray) -> object:
        with self._detector_lock:
            return self._detector.detect_lower(frame)

    def get_snapshot(self) -> VisionSnapshot:
        """현재 순간의 freshness를 반영한 immutable snapshot을 반환한다."""

        return self._compose_snapshot()

    def get_status(self) -> VisionStatusResponse:
        snapshot = self.get_snapshot()
        now_mono, now_wall = self._monotonic(), self._utc_now()
        upper_status = self._camera_response(snapshot.upper, now_mono, now_wall)
        lower_status = self._camera_response(snapshot.lower, now_mono, now_wall)
        observed = self._latest_observed(snapshot)
        expires = observed + timedelta(seconds=self._settings.result_stale_after_seconds) if observed else None
        return VisionStatusResponse(
            cameras={"upper": upper_status, "lower": lower_status},
            identity=IdentityResponse(),
            presence=PresenceResponse(
                raw_status=snapshot.raw_presence,
                status=snapshot.stable_presence,
                upper_count=snapshot.upper.count,
                lower_count=snapshot.lower.count,
                observed_at=observed,
                expires_at=expires,
            ),
            posture=PostureResponse(
                raw_status=snapshot.raw_posture,
                status=snapshot.stable_posture,
                candidate_since=snapshot.posture_candidate_since,
                observed_at=snapshot.lower.observed_at,
                expires_at=(snapshot.lower.observed_at + timedelta(seconds=self._settings.result_stale_after_seconds)) if snapshot.lower.observed_at else None,
            ),
            association=AssociationResponse(
                usable=snapshot.usable, reason_codes=list(snapshot.reason_codes)
            ),
        )

    def get_fresh_face_observation(self) -> FreshFaceObservation | None:
        """Task 05가 재검출 없이 소비하는, 아직 fresh한 상단 face box 결과다."""

        upper = self.get_snapshot().upper
        observation = upper.face_observation
        if (
            observation is None
            or not upper.connected
            or upper.detector_error
            or not self._is_fresh(upper)
        ):
            return None
        return observation

    def _new_frame(self, source: RtspFrameSource | None, previous: float | None) -> LatestFrame | None:
        if source is None:
            return None
        frame = source.get_latest_frame()
        if frame is None or frame[1] == previous:
            return None
        return frame

    def _lower_inference_due(self) -> bool:
        previous = self._last_lower_inference_monotonic
        return previous is None or (
            self._monotonic() - previous >= self._settings.lower_inference_interval_seconds
        )

    def _make_upper_observation(self, frame: LatestFrame, result: object) -> CameraObservation:
        now, wall = self._monotonic(), self._utc_now()
        if isinstance(result, BaseException) or not isinstance(result, UpperDetection):
            return CameraObservation(
                True, frame[1], now, wall, self._error_message(result), detector_error=True
            )
        face = FreshFaceObservation(frame[0], result.face_boxes, frame[1], wall)
        return CameraObservation(True, frame[1], now, wall, None, result.count, face_observation=face)

    def _make_lower_observation(self, frame: LatestFrame, result: object) -> CameraObservation:
        now, wall = self._monotonic(), self._utc_now()
        if isinstance(result, BaseException) or not isinstance(result, LowerDetection):
            return CameraObservation(
                True, frame[1], now, wall, self._error_message(result), detector_error=True
            )
        return CameraObservation(True, frame[1], now, wall, None, result.count, result.posture)

    @staticmethod
    def _error_message(result: object) -> str:
        if isinstance(result, BaseException):
            return str(result).strip() or type(result).__name__
        return "invalid detector result"

    def _empty_observation(self, source: RtspFrameSource | None) -> CameraObservation:
        return CameraObservation(source is not None and source.is_connected(), None, None, None, source.get_last_error() if source else "source not configured")

    def _has_distinct_combined_pair(self) -> bool:
        """이전 결합 뒤 양쪽 source가 모두 새 frame을 낸 때만 timer를 전진한다."""

        upper, lower = self._upper.captured_monotonic, self._lower.captured_monotonic
        if upper is None or lower is None:
            return False
        pair = (upper, lower)
        previous = self._last_combined_pair
        if previous is not None and (pair == previous or upper == previous[0] or lower == previous[1]):
            return False
        self._last_combined_pair = pair
        return True

    def _compose_snapshot(self, *, force_stale: bool = False, advance: bool = False) -> VisionSnapshot:
        upper = self._current_camera(self._upper_source, self._upper)
        lower = self._current_camera(self._lower_source, self._lower)
        reason_codes = self._reason_codes(upper, lower, force_stale)
        hard_reasons = tuple(code for code in reason_codes if code is not BlockCode.POSTURE_UNASSOCIATED)
        raw_presence = self._raw_presence(upper, lower, hard_reasons)
        # 하단 raw 자세는 관측성용으로 독립 노출한다. 상단 detector가 아직 unavailable이어도
        # fresh singleton 하단 frame은 볼 수 있지만, usable/stable 결합 안전 조건은 완화하지 않는다.
        raw_posture = (
            lower.posture
            if (
                lower.connected
                and lower.count == 1
                and not lower.detector_error
                and self._is_fresh(lower)
            )
            else PostureStatus.UNKNOWN
        )
        if force_stale or hard_reasons:
            self._presence_candidate = None
            self._posture_candidate = None
            self._stable_presence = PresenceStatus.UNKNOWN
            self._stable_posture = PostureStatus.UNKNOWN
        elif advance:
            self._stable_presence = self._stabilize_presence(raw_presence)
            if raw_posture is PostureStatus.UNKNOWN:
                self._posture_candidate = None
                self._stable_posture = PostureStatus.UNKNOWN
            else:
                self._stable_posture = self._stabilize_posture(raw_posture)
        usable = not reason_codes and self._stable_presence is PresenceStatus.PRESENT_SINGLE and self._stable_posture is not PostureStatus.UNKNOWN
        if not usable and not reason_codes:
            reason_codes = (BlockCode.PRESENCE_NOT_SINGLE,) if self._stable_presence is not PresenceStatus.PRESENT_SINGLE else (BlockCode.POSTURE_UNKNOWN,)
        return VisionSnapshot(upper, lower, raw_presence, self._stable_presence, raw_posture, self._stable_posture, self._candidate_wall(self._presence_candidate), self._candidate_wall(self._posture_candidate), usable, tuple(reason_codes))

    def _current_camera(self, source: RtspFrameSource | None, observation: CameraObservation) -> CameraObservation:
        if source is None:
            return CameraObservation(False, None, None, None, "source not configured")
        return CameraObservation(
            source.is_connected(),
            observation.captured_monotonic,
            observation.observed_monotonic,
            observation.observed_at,
            observation.error or source.get_last_error(),
            observation.count,
            observation.posture,
            observation.face_observation,
            observation.detector_error,
        )

    def _reason_codes(self, upper: CameraObservation, lower: CameraObservation, force_stale: bool) -> tuple[BlockCode, ...]:
        codes: list[BlockCode] = []
        for label, observation, unavailable, stale in (("upper", upper, BlockCode.UPPER_CAMERA_UNAVAILABLE, BlockCode.UPPER_FRAME_STALE), ("lower", lower, BlockCode.LOWER_CAMERA_UNAVAILABLE, BlockCode.LOWER_FRAME_STALE)):
            if not observation.connected:
                codes.append(unavailable)
            elif force_stale or not self._is_fresh(observation):
                codes.append(stale)
            if observation.detector_error:
                codes.append(BlockCode.MODEL_ERROR)
        if upper.count is None or lower.count is None:
            codes.append(BlockCode.MODEL_UNAVAILABLE)
        else:
            if upper.count > 1 or lower.count > 1:
                codes.append(BlockCode.MULTIPLE_PEOPLE)
            if upper.count != lower.count:
                codes.append(BlockCode.COUNT_MISMATCH)
        if upper.captured_monotonic is not None and lower.captured_monotonic is not None and abs(upper.captured_monotonic - lower.captured_monotonic) > self._settings.max_camera_skew_seconds:
            codes.append(BlockCode.CAMERA_TIMESTAMP_MISMATCH)
        if lower.posture is PostureStatus.UNKNOWN:
            codes.append(BlockCode.POSTURE_UNASSOCIATED)
        return tuple(dict.fromkeys(codes))

    def _raw_presence(self, upper: CameraObservation, lower: CameraObservation, reasons: tuple[BlockCode, ...]) -> PresenceStatus:
        if BlockCode.MULTIPLE_PEOPLE in reasons:
            return PresenceStatus.MULTIPLE
        if reasons:
            return PresenceStatus.UNKNOWN
        assert upper.count is not None and lower.count is not None
        return PresenceStatus.VACANT if upper.count == 0 else PresenceStatus.PRESENT_SINGLE

    def _stabilize_presence(self, value: PresenceStatus) -> PresenceStatus:
        self._presence_candidate = self._advance_candidate(self._presence_candidate, value)
        candidate = self._presence_candidate
        if candidate and self._monotonic() - candidate[1] >= self._settings.stable_after_seconds:
            return value
        return PresenceStatus.UNKNOWN

    def _stabilize_posture(self, value: PostureStatus) -> PostureStatus:
        self._posture_candidate = self._advance_candidate(self._posture_candidate, value)
        candidate = self._posture_candidate
        if candidate and self._monotonic() - candidate[1] >= self._settings.stable_after_seconds:
            return value
        return PostureStatus.UNKNOWN

    def _advance_candidate(self, candidate, value):  # type: ignore[no-untyped-def]
        if candidate is not None and candidate[0] == value:
            return candidate
        return (value, self._monotonic(), self._utc_now())

    @staticmethod
    def _candidate_wall(candidate):  # type: ignore[no-untyped-def]
        return candidate[2] if candidate else None

    def _is_fresh(self, observation: CameraObservation) -> bool:
        if observation.captured_monotonic is None or observation.observed_monotonic is None:
            return False
        now = self._monotonic()
        return now - observation.captured_monotonic <= self._settings.frame_stale_after_seconds and now - observation.observed_monotonic <= self._settings.result_stale_after_seconds

    def _camera_response(self, observation: CameraObservation, now_mono: float, now_wall: datetime) -> CameraStatusResponse:
        if not observation.connected:
            state = CameraStatus.ERROR if observation.error else CameraStatus.OFFLINE
        elif observation.error:
            state = CameraStatus.ERROR
        elif not self._is_fresh(observation):
            state = CameraStatus.STALE
        else:
            state = CameraStatus.ONLINE
        age = now_mono - observation.captured_monotonic if observation.captured_monotonic is not None else None
        expires = observation.observed_at + timedelta(seconds=self._settings.result_stale_after_seconds) if observation.observed_at else None
        return CameraStatusResponse(status=state, observed_at=observation.observed_at, expires_at=expires, age_seconds=age, error=observation.error)

    @staticmethod
    def _latest_observed(snapshot: VisionSnapshot) -> datetime | None:
        values = (snapshot.upper.observed_at, snapshot.lower.observed_at)
        return max((value for value in values if value is not None), default=None)
