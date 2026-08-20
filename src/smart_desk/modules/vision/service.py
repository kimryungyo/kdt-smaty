"""두 WebRTC 최신 frame을 결합하는 fail-closed Vision service."""

from __future__ import annotations

import asyncio
from collections import Counter, deque
from datetime import UTC, datetime, timedelta
import threading
import time
from typing import Callable, Protocol

import numpy as np

from smart_desk.config.settings import VisionSettings
from smart_desk.modules.media import LatestFrame
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
    VisionDebugCameraResponse,
    VisionDebugResponse,
    VisionStatusResponse,
    DebugBoxResponse,
    DebugKeypointResponse,
    DebugPoseResponse,
)


class FrameSource(Protocol):
    """Vision이 요구하는 transport-neutral 최신 frame 계약이다."""

    def get_latest_frame(self) -> LatestFrame | None: ...
    def is_connected(self) -> bool: ...
    def get_last_error(self) -> str | None: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


class VisionService:
    """source별 최신 frame만 추론하고 불변 snapshot으로 결합한다."""

    def __init__(
        self,
        *,
        upper_source: FrameSource | None,
        lower_source: FrameSource | None,
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
        self._last_upper_inference_monotonic: float | None = None
        self._last_lower_inference_monotonic: float | None = None
        self._upper = self._empty_observation(upper_source)
        self._lower = self._empty_observation(lower_source)
        self._presence_window: deque[tuple[float, datetime, PresenceStatus]] = deque(
            maxlen=1000
        )
        self._posture_window: deque[tuple[float, datetime, PostureStatus]] = deque(
            maxlen=1000
        )
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
        self._presence_window.clear()
        self._posture_window.clear()
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
        if upper_frame is not None and self._upper_inference_due():
            kinds.append(("upper", upper_frame))
            jobs.append(asyncio.to_thread(self._detect_upper, upper_frame[0]))
        if lower_frame is not None and self._lower_inference_due():
            kinds.append(("lower", lower_frame))
            jobs.append(asyncio.to_thread(self._detect_lower, lower_frame[0]))
        upper_advanced = lower_advanced = False
        if jobs:
            results = await asyncio.gather(*jobs, return_exceptions=True)
            for (kind, frame), result in zip(kinds, results, strict=True):
                if kind == "upper":
                    self._last_upper_capture = frame[1]
                    self._last_upper_inference_monotonic = self._monotonic()
                    self._upper = self._make_upper_observation(frame, result)
                    upper_advanced = True
                else:
                    self._last_lower_capture = frame[1]
                    self._last_lower_inference_monotonic = self._monotonic()
                    self._lower = self._make_lower_observation(frame, result)
                    lower_advanced = True
        self._snapshot = self._compose_snapshot(
            advance_presence=upper_advanced, advance_posture=lower_advanced
        )

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

    def get_debug(self) -> VisionDebugResponse:
        """마지막 성공 추론 frame의 geometry만 반환한다. raw image는 별도 JPEG endpoint다."""

        snapshot = self.get_snapshot()
        return VisionDebugResponse(
            cameras={
                "upper": self._debug_camera_response(snapshot.upper),
                "lower": self._debug_camera_response(snapshot.lower),
            }
        )

    def get_debug_frame(self, camera: str) -> np.ndarray | None:
        """마지막 성공 추론 frame 복사본. 요청 처리 중 detector/source와 공유하지 않는다."""

        if camera not in {"upper", "lower"}:
            return None
        observation = getattr(self.get_snapshot(), camera)
        return observation.debug_frame.copy() if observation.debug_frame is not None else None

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

    def _new_frame(self, source: FrameSource | None, previous: float | None) -> LatestFrame | None:
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

    def _upper_inference_due(self) -> bool:
        previous = self._last_upper_inference_monotonic
        return previous is None or (
            self._monotonic() - previous >= self._settings.upper_inference_interval_seconds
        )

    def _make_upper_observation(self, frame: LatestFrame, result: object) -> CameraObservation:
        now, wall = self._monotonic(), self._utc_now()
        if isinstance(result, BaseException) or not isinstance(result, UpperDetection):
            return CameraObservation(
                True, frame[1], now, wall, self._error_message(result), detector_error=True
            )
        face = FreshFaceObservation(frame[0], result.face_boxes, frame[1], wall)
        return CameraObservation(
            True, frame[1], now, wall, None, result.count, face_observation=face,
            frame_width=int(frame[0].shape[1]), frame_height=int(frame[0].shape[0]),
            person_boxes=result.person_boxes, debug_frame=frame[0].copy(),
        )

    def _make_lower_observation(self, frame: LatestFrame, result: object) -> CameraObservation:
        now, wall = self._monotonic(), self._utc_now()
        if isinstance(result, BaseException) or not isinstance(result, LowerDetection):
            return CameraObservation(
                True, frame[1], now, wall, self._error_message(result), detector_error=True
            )
        return CameraObservation(
            True, frame[1], now, wall, None, result.count, result.posture,
            frame_width=int(frame[0].shape[1]), frame_height=int(frame[0].shape[0]),
            pose_detections=result.pose_detections, debug_frame=frame[0].copy(),
        )

    @staticmethod
    def _error_message(result: object) -> str:
        if isinstance(result, BaseException):
            return str(result).strip() or type(result).__name__
        return "invalid detector result"

    def _empty_observation(self, source: FrameSource | None) -> CameraObservation:
        return CameraObservation(source is not None and source.is_connected(), None, None, None, source.get_last_error() if source else "source not configured")

    def _compose_snapshot(
        self,
        *,
        force_stale: bool = False,
        advance_presence: bool = False,
        advance_posture: bool = False,
    ) -> VisionSnapshot:
        upper = self._current_camera(self._upper_source, self._upper)
        lower = self._current_camera(self._lower_source, self._lower)
        observed_reasons = self._reason_codes(upper, lower, force_stale)
        # 재실은 상단 detector만 소유한다. 하단 camera/pose 오류는 자세와 association을
        # 차단하지만 상단의 VACANT/PRESENT/MULTIPLE 관측을 UNKNOWN으로 덮지 않는다.
        presence_reasons = tuple(
            code
            for code in observed_reasons
            if code in {
                BlockCode.UPPER_CAMERA_UNAVAILABLE,
                BlockCode.UPPER_FRAME_STALE,
                BlockCode.MODEL_UNAVAILABLE,
                BlockCode.MULTIPLE_PEOPLE,
            }
        )
        raw_presence = self._raw_presence(upper, presence_reasons)
        # 하단은 재실 count와 무관하게 최고 confidence pose 한 명의 자세만 공개한다.
        raw_posture = (
            lower.posture
            if (
                lower.connected
                and not lower.detector_error
                and self._is_fresh(lower)
            )
            else PostureStatus.UNKNOWN
        )
        immediate_reasons = tuple(
            code
            for code in observed_reasons
            if code
            in {
                BlockCode.UPPER_CAMERA_UNAVAILABLE,
                BlockCode.LOWER_CAMERA_UNAVAILABLE,
                BlockCode.UPPER_FRAME_STALE,
                BlockCode.LOWER_FRAME_STALE,
            }
        )
        upper_immediate = tuple(
            code for code in immediate_reasons
            if code in {BlockCode.UPPER_CAMERA_UNAVAILABLE, BlockCode.UPPER_FRAME_STALE}
        )
        lower_immediate = tuple(
            code for code in immediate_reasons
            if code in {BlockCode.LOWER_CAMERA_UNAVAILABLE, BlockCode.LOWER_FRAME_STALE}
        )
        if force_stale or upper_immediate:
            self._presence_window.clear()
            self._stable_presence = PresenceStatus.UNKNOWN
        if force_stale or lower_immediate:
            self._posture_window.clear()
            self._stable_posture = PostureStatus.UNKNOWN
        if not (force_stale or upper_immediate) and advance_presence:
            self._stable_presence = self._stabilize_presence(raw_presence)
        if not (force_stale or lower_immediate) and advance_posture:
            self._stable_posture = self._stabilize_posture(raw_posture)
        # usable은 "관측을 믿을 수 있는가"만 뜻한다. 사람이 몇 명인지는 여기서
        # 따지지 않고 stable_presence로 노출해, 소비자가 정책에 맞게 판단한다.
        # 여럿이 보인다고 관측이 틀린 것은 아니기 때문이다.
        usable = (
            not immediate_reasons
            and self._stable_presence in (PresenceStatus.PRESENT_SINGLE,
                                          PresenceStatus.MULTIPLE)
            and self._stable_posture is not PostureStatus.UNKNOWN
        )
        reason_codes = self._effective_reason_codes(
            observed_reasons, immediate_reasons
        )
        return VisionSnapshot(
            upper,
            lower,
            raw_presence,
            self._stable_presence,
            raw_posture,
            self._stable_posture,
            self._candidate_wall(self._presence_window),
            self._candidate_wall(self._posture_window),
            usable,
            reason_codes,
        )

    def _current_camera(self, source: FrameSource | None, observation: CameraObservation) -> CameraObservation:
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
            observation.frame_width,
            observation.frame_height,
            observation.person_boxes,
            observation.pose_detections,
            observation.debug_frame,
        )

    @staticmethod
    def _debug_box(box) -> DebugBoxResponse:  # type: ignore[no-untyped-def]
        return DebugBoxResponse(
            x=box.x, y=box.y, width=box.width, height=box.height,
            confidence=box.confidence,
        )

    def _debug_camera_response(self, observation: CameraObservation) -> VisionDebugCameraResponse:
        return VisionDebugCameraResponse(
            observed_at=observation.observed_at,
            frame_width=observation.frame_width,
            frame_height=observation.frame_height,
            person_boxes=[self._debug_box(box) for box in observation.person_boxes],
            face_boxes=[self._debug_box(box) for box in observation.face_observation.boxes]
            if observation.face_observation else [],
            pose_detections=[
                DebugPoseResponse(
                    box=self._debug_box(pose.box),
                    keypoints=[
                        DebugKeypointResponse(x=point.x, y=point.y, confidence=point.confidence)
                        for point in pose.keypoints
                    ],
                )
                for pose in observation.pose_detections
            ],
            detector_error=observation.detector_error,
            error=observation.error,
            frame_available=observation.debug_frame is not None,
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
        if upper.count is None:
            codes.append(BlockCode.MODEL_UNAVAILABLE)
        else:
            if upper.count > 1:
                codes.append(BlockCode.MULTIPLE_PEOPLE)
        if upper.captured_monotonic is not None and lower.captured_monotonic is not None and abs(upper.captured_monotonic - lower.captured_monotonic) > self._settings.max_camera_skew_seconds:
            codes.append(BlockCode.CAMERA_TIMESTAMP_MISMATCH)
        if lower.posture is PostureStatus.UNKNOWN:
            codes.append(BlockCode.POSTURE_UNASSOCIATED)
        return tuple(dict.fromkeys(codes))

    def _raw_presence(self, upper: CameraObservation, reasons: tuple[BlockCode, ...]) -> PresenceStatus:
        if BlockCode.MULTIPLE_PEOPLE in reasons:
            return PresenceStatus.MULTIPLE
        if reasons:
            return PresenceStatus.UNKNOWN
        assert upper.count is not None
        return PresenceStatus.VACANT if upper.count == 0 else PresenceStatus.PRESENT_SINGLE

    def _stabilize_presence(self, value: PresenceStatus) -> PresenceStatus:
        return self._stabilize_value(
            self._presence_window,
            value,
            self._stable_presence,
            PresenceStatus.UNKNOWN,
        )

    def _stabilize_posture(self, value: PostureStatus) -> PostureStatus:
        """Use recent distinct samples, rather than wall time, for posture UX."""

        self._posture_window.append((self._monotonic(), self._utc_now(), value))
        while len(self._posture_window) > self._settings.posture_unknown_samples:
            self._posture_window.popleft()

        stable = self._stable_posture
        if stable is PostureStatus.UNKNOWN:
            return self._recover_posture_from_unknown()
        if value is PostureStatus.UNKNOWN:
            samples = self._recent_posture_samples(self._settings.posture_unknown_samples)
            if len(samples) == self._settings.posture_unknown_samples and all(
                item[2] is PostureStatus.UNKNOWN for item in samples
            ):
                self._stable_posture = PostureStatus.UNKNOWN
            return self._stable_posture
        if value is stable:
            return stable

        samples = self._recent_posture_samples(self._settings.posture_transition_samples)
        if sum(item[2] is value for item in samples) >= self._settings.posture_transition_required_samples:
            self._stable_posture = value
        return self._stable_posture

    def _recover_posture_from_unknown(self) -> PostureStatus:
        samples = self._recent_posture_samples(self._settings.posture_recovery_samples)
        if len(samples) != self._settings.posture_recovery_samples:
            return PostureStatus.UNKNOWN
        candidate = samples[-1][2]
        if candidate is not PostureStatus.UNKNOWN and all(item[2] is candidate for item in samples):
            self._stable_posture = candidate
        return self._stable_posture

    def _recent_posture_samples(self, count: int):
        return list(self._posture_window)[-count:]

    def _stabilize_value(self, window, value, stable, unknown):  # type: ignore[no-untyped-def]
        """3초 관측 묶음의 다수결로 전이하고 그동안 기존 stable 값을 유지한다."""

        if not window and value is stable:
            return stable
        now = self._monotonic()
        window.append((now, self._utc_now(), value))
        if now - window[0][0] < self._settings.stable_after_seconds:
            return stable
        if len(window) < self._settings.stability_min_samples:
            return stable
        counts = Counter(item[2] for item in window)
        winner, votes = counts.most_common(1)[0]
        if votes / len(window) >= self._settings.stability_majority_ratio:
            result = winner
        elif stable is not unknown:
            # 다수결이 갈렸다고 곧바로 UNKNOWN으로 떨어뜨리면, 검출이 한두 프레임
            # 흔들리는 것만으로 자동 제어가 끊기고 세션까지 흔들린다. 확신이
            # 없을 뿐 관측 자체는 계속되고 있으므로, 새 합의가 설 때까지 직전
            # 값을 유지한다. 관측이 끊기는 경우는 호출부가 따로 UNKNOWN으로 만든다.
            result = stable
        else:
            result = unknown
        window.clear()
        return result

    @staticmethod
    def _candidate_wall(window):  # type: ignore[no-untyped-def]
        return window[0][1] if window else None

    def _effective_reason_codes(
        self,
        observed: tuple[BlockCode, ...],
        immediate: tuple[BlockCode, ...],
    ) -> tuple[BlockCode, ...]:
        """순간 추론 이상은 raw에 남기고 안정화된 제어 차단 사유만 반환한다."""

        if immediate:
            return tuple(
                code
                for code in observed
                if code in immediate
                or code in {BlockCode.MODEL_ERROR, BlockCode.MODEL_UNAVAILABLE}
            )
        if self._stable_presence is PresenceStatus.MULTIPLE:
            return (BlockCode.MULTIPLE_PEOPLE,)
        if self._stable_presence is not PresenceStatus.PRESENT_SINGLE:
            for code in (
                BlockCode.MODEL_ERROR,
                BlockCode.MODEL_UNAVAILABLE,
                BlockCode.CAMERA_TIMESTAMP_MISMATCH,
            ):
                if code in observed:
                    return (code,)
            return (BlockCode.PRESENCE_NOT_SINGLE,)
        if self._stable_posture is PostureStatus.UNKNOWN:
            return (BlockCode.POSTURE_UNKNOWN,)
        return ()

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
