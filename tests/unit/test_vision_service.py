"""Vision의 distinct frame, fail-closed, executor/lifecycle 경계를 검증한다."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import threading
import time

import numpy as np

from smart_desk.config.settings import VisionSettings
from smart_desk.modules.vision import FaceBox, LowerDetection, PostureStatus, UpperDetection
from smart_desk.modules.vision.models import BlockCode, PresenceStatus
from smart_desk.modules.vision.service import VisionService


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def utc_now(self) -> datetime:
        return datetime(2026, 8, 17, tzinfo=UTC)


class FakeSource:
    def __init__(self) -> None:
        self.frame: tuple[np.ndarray, float] | None = None
        self.connected = True
        self.error: str | None = None

    def get_latest_frame(self):  # type: ignore[no-untyped-def]
        return self.frame

    def is_connected(self) -> bool:
        return self.connected

    def get_last_error(self) -> str | None:
        return self.error


class FakeDetector:
    def __init__(self, upper: UpperDetection, lower: LowerDetection) -> None:
        self.upper = upper
        self.lower = lower
        self.upper_calls = 0
        self.lower_calls = 0
        self.thread_ids: list[int] = []

    def detect_upper(self, _frame: np.ndarray) -> UpperDetection:
        self.upper_calls += 1
        self.thread_ids.append(threading.get_ident())
        return self.upper

    def detect_lower(self, _frame: np.ndarray) -> LowerDetection:
        self.lower_calls += 1
        self.thread_ids.append(threading.get_ident())
        return self.lower


def make_service(
    clock: Clock, detector: FakeDetector, upper: FakeSource, lower: FakeSource
) -> VisionService:
    return VisionService(
        upper_source=upper,  # type: ignore[arg-type]
        lower_source=lower,  # type: ignore[arg-type]
        detector=detector,
        settings=VisionSettings(
            stable_after_seconds=3,
            frame_stale_after_seconds=2,
            result_stale_after_seconds=2,
            upper_inference_interval_seconds=0.5,
            lower_inference_interval_seconds=0.5,
        ),
        monotonic=clock.monotonic,
        utc_now=clock.utc_now,
    )


async def test_same_frame_cannot_complete_stabilization() -> None:
    clock, upper, lower = Clock(), FakeSource(), FakeSource()
    detector = FakeDetector(UpperDetection(body_count=1, face_boxes=(FaceBox(0, 0, 1, 1),)), LowerDetection(1, PostureStatus.SITTING))
    upper.frame = (np.zeros((1, 1)), 0.0)
    lower.frame = (np.zeros((1, 1)), 0.0)
    service = make_service(clock, detector, upper, lower)

    await service.process_once()
    clock.value = 1.5
    await service.process_once()

    snapshot = service.get_snapshot()
    assert detector.upper_calls == detector.lower_calls == 1
    assert snapshot.raw_presence is PresenceStatus.PRESENT_SINGLE
    assert snapshot.stable_presence is PresenceStatus.UNKNOWN


async def test_upper_presence_stabilizes_without_new_lower_frame_but_auto_stays_blocked() -> None:
    clock, upper, lower = Clock(), FakeSource(), FakeSource()
    detector = FakeDetector(UpperDetection(body_count=1), LowerDetection(1, PostureStatus.SITTING))
    service = make_service(clock, detector, upper, lower)

    upper.frame = lower.frame = (np.zeros((1, 1)), 0.0)
    await service.process_once()
    for value in (1.0, 3.1, 4.0):
        clock.value = value
        upper.frame = (np.zeros((1, 1)), value)
        await service.process_once()

    snapshot = service.get_snapshot()
    assert snapshot.raw_presence is PresenceStatus.PRESENT_SINGLE
    assert snapshot.stable_presence is PresenceStatus.PRESENT_SINGLE
    assert snapshot.stable_posture is PostureStatus.UNKNOWN
    assert BlockCode.LOWER_FRAME_STALE in snapshot.reason_codes


async def test_distinct_frames_stabilize_and_face_result_is_shared() -> None:
    clock, upper, lower = Clock(), FakeSource(), FakeSource()
    box = FaceBox(1, 2, 3, 4)
    detector = FakeDetector(UpperDetection(body_count=1, face_boxes=(box,)), LowerDetection(1, PostureStatus.STANDING))
    service = make_service(clock, detector, upper, lower)

    for value in (0.0, 1.0, 3.1):
        clock.value = value
        upper.frame = (np.zeros((1, 1)), value)
        lower.frame = (np.zeros((1, 1)), value)
        await service.process_once()

    snapshot = service.get_snapshot()
    face = service.get_fresh_face_observation()
    assert snapshot.usable is True
    assert snapshot.stable_presence is PresenceStatus.PRESENT_SINGLE
    assert snapshot.stable_posture is PostureStatus.STANDING
    assert face is not None and face.boxes == (box,)
    assert detector.upper_calls == 3


async def test_multiple_skew_stale_and_detector_error_are_fail_closed() -> None:
    clock, upper, lower = Clock(), FakeSource(), FakeSource()
    detector = FakeDetector(UpperDetection(body_count=2), LowerDetection(1, PostureStatus.SITTING))
    service = make_service(clock, detector, upper, lower)
    upper.frame, lower.frame = (np.zeros((1, 1)), 0.0), (np.zeros((1, 1)), 0.0)
    await service.process_once()
    # 첫 raw MULTIPLE은 안정화 전이므로 AUTO를 즉시 STOP시키지 않는다.
    assert service.get_snapshot().reason_codes == (BlockCode.PRESENCE_NOT_SINGLE,)

    detector.upper, detector.lower = UpperDetection(body_count=1), LowerDetection(1, PostureStatus.SITTING)
    clock.value = 0.5
    upper.frame, lower.frame = (np.zeros((1, 1)), 0.5), (np.zeros((1, 1)), 1.5)
    await service.process_once()
    assert BlockCode.CAMERA_TIMESTAMP_MISMATCH in service.get_snapshot().reason_codes

    clock.value = 4.0
    assert BlockCode.UPPER_FRAME_STALE in service.get_snapshot().reason_codes


async def test_lower_count_is_not_an_association_input_and_detector_errors_fail_closed() -> None:
    clock, upper, lower = Clock(), FakeSource(), FakeSource()
    detector = FakeDetector(
        UpperDetection(body_count=1, face_boxes=(FaceBox(0, 0, 1, 1),)),
        LowerDetection(2, PostureStatus.SITTING),
    )
    service = make_service(clock, detector, upper, lower)
    upper.frame = lower.frame = (np.zeros((1, 1)), 0.0)
    await service.process_once()
    first = service.get_snapshot()
    assert BlockCode.COUNT_MISMATCH not in first.reason_codes
    assert first.raw_presence is PresenceStatus.PRESENT_SINGLE
    assert first.raw_posture is PostureStatus.SITTING

    clock.value = 0.1
    upper.connected = False
    assert BlockCode.UPPER_CAMERA_UNAVAILABLE in service.get_snapshot().reason_codes
    assert BlockCode.MODEL_ERROR not in service.get_snapshot().reason_codes
    assert service.get_fresh_face_observation() is None

    upper.connected = True
    detector.upper = RuntimeError("detector unavailable")  # type: ignore[assignment]
    clock.value = 0.6
    upper.frame = lower.frame = (np.zeros((1, 1)), 0.6)
    await service.process_once()
    assert BlockCode.MODEL_ERROR in service.get_snapshot().reason_codes
    assert service.get_fresh_face_observation() is None


async def test_unknown_recovers_to_a_consistent_posture_after_one_second() -> None:
    clock, upper, lower = Clock(), FakeSource(), FakeSource()
    detector = FakeDetector(UpperDetection(body_count=1), LowerDetection(1, PostureStatus.SITTING))
    service = make_service(clock, detector, upper, lower)

    for value in (0.0, 1.0):
        clock.value = value
        upper.frame = lower.frame = (np.zeros((1, 1)), value)
        await service.process_once()
    detector.lower = LowerDetection(1, PostureStatus.STANDING)
    clock.value = 3.1
    upper.frame = lower.frame = (np.zeros((1, 1)), 3.1)
    await service.process_once()

    snapshot = service.get_snapshot()
    assert snapshot.raw_posture is PostureStatus.STANDING
    assert snapshot.stable_posture is PostureStatus.SITTING
    assert snapshot.posture_candidate_since is not None


async def test_stable_values_ignore_brief_multiple_and_unknown_observations() -> None:
    clock, upper, lower = Clock(), FakeSource(), FakeSource()
    detector = FakeDetector(
        UpperDetection(body_count=1), LowerDetection(1, PostureStatus.STANDING)
    )
    service = make_service(clock, detector, upper, lower)

    for value in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0):
        clock.value = value
        upper.frame = lower.frame = (np.zeros((1, 1)), value)
        await service.process_once()
    assert service.get_snapshot().usable is True

    clock.value = 3.5
    detector.upper = UpperDetection(body_count=2)
    upper.frame = lower.frame = (np.zeros((1, 1)), 3.5)
    await service.process_once()
    transient = service.get_snapshot()
    assert transient.raw_presence is PresenceStatus.MULTIPLE
    assert transient.stable_presence is PresenceStatus.PRESENT_SINGLE
    assert transient.stable_posture is PostureStatus.STANDING
    assert transient.usable is True
    assert transient.reason_codes == ()

    clock.value = 4.0
    detector.upper = UpperDetection(body_count=1)
    detector.lower = LowerDetection(0, PostureStatus.UNKNOWN)
    upper.frame = lower.frame = (np.zeros((1, 1)), 4.0)
    await service.process_once()
    lower_unknown = service.get_snapshot()
    assert lower_unknown.raw_presence is PresenceStatus.PRESENT_SINGLE
    assert lower_unknown.raw_posture is PostureStatus.UNKNOWN
    # 책상 이동 중 한 frame pose 누락은 이미 안정화된 자세/재실을 무효화하지 않는다.
    # 다중 인원과 camera freshness는 별도로 즉시 fail-closed 처리한다.
    assert lower_unknown.usable is True
    assert lower_unknown.reason_codes == ()

    detector.lower = LowerDetection(1, PostureStatus.STANDING)
    for value in (4.5, 5.0, 5.5, 6.0, 6.5):
        clock.value = value
        upper.frame = lower.frame = (np.zeros((1, 1)), value)
        await service.process_once()

    snapshot = service.get_snapshot()
    assert snapshot.stable_presence is PresenceStatus.PRESENT_SINGLE
    assert snapshot.stable_posture is PostureStatus.STANDING
    assert snapshot.usable is True


async def test_persistent_unknown_posture_blocks_only_after_stability_window() -> None:
    clock, upper, lower = Clock(), FakeSource(), FakeSource()
    detector = FakeDetector(
        UpperDetection(body_count=1), LowerDetection(1, PostureStatus.STANDING)
    )
    service = make_service(clock, detector, upper, lower)

    for value in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0):
        clock.value = value
        upper.frame = lower.frame = (np.zeros((1, 1)), value)
        await service.process_once()
    assert service.get_snapshot().usable is True

    detector.lower = LowerDetection(0, PostureStatus.UNKNOWN)
    for value in (3.5, 4.0, 4.5, 5.0, 5.5):
        clock.value = value
        upper.frame = lower.frame = (np.zeros((1, 1)), value)
        await service.process_once()
        assert service.get_snapshot().usable is True

    # 최근 6개 sample이 모두 UNKNOWN인 시점부터만 차단한다.
    clock.value = 6.0
    upper.frame = lower.frame = (np.zeros((1, 1)), 6.0)
    await service.process_once()
    snapshot = service.get_snapshot()
    assert snapshot.stable_posture is PostureStatus.UNKNOWN
    assert snapshot.usable is False
    assert snapshot.reason_codes == (BlockCode.POSTURE_UNKNOWN,)


async def test_posture_changes_after_two_second_rolling_window() -> None:
    clock, upper, lower = Clock(), FakeSource(), FakeSource()
    detector = FakeDetector(
        UpperDetection(body_count=1), LowerDetection(1, PostureStatus.SITTING)
    )
    service = make_service(clock, detector, upper, lower)

    for value in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0):
        clock.value = value
        upper.frame = lower.frame = (np.zeros((1, 1)), value)
        await service.process_once()
    assert service.get_snapshot().stable_posture is PostureStatus.SITTING

    detector.lower = LowerDetection(1, PostureStatus.STANDING)
    for value in (3.5, 4.0, 4.5):
        clock.value = value
        upper.frame = lower.frame = (np.zeros((1, 1)), value)
        await service.process_once()
    detector.lower = LowerDetection(1, PostureStatus.UNKNOWN)
    clock.value = 5.0
    upper.frame = lower.frame = (np.zeros((1, 1)), 5.0)
    await service.process_once()
    detector.lower = LowerDetection(1, PostureStatus.STANDING)
    for value in (5.5, 6.0, 6.5, 7.0, 7.5):
        clock.value = value
        upper.frame = lower.frame = (np.zeros((1, 1)), value)
        await service.process_once()

    snapshot = service.get_snapshot()
    assert snapshot.raw_posture is PostureStatus.STANDING
    assert snapshot.stable_posture is PostureStatus.STANDING
    assert snapshot.usable is True


async def test_persistent_multiple_wins_window_and_blocks_vision() -> None:
    clock, upper, lower = Clock(), FakeSource(), FakeSource()
    detector = FakeDetector(
        UpperDetection(body_count=1), LowerDetection(1, PostureStatus.SITTING)
    )
    service = make_service(clock, detector, upper, lower)

    for value in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0):
        clock.value = value
        upper.frame = lower.frame = (np.zeros((1, 1)), value)
        await service.process_once()
    detector.upper = UpperDetection(body_count=2)
    detector.lower = LowerDetection(2, PostureStatus.UNKNOWN)
    for value in (3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0, 8.5):
        clock.value = value
        upper.frame = lower.frame = (np.zeros((1, 1)), value)
        await service.process_once()

    snapshot = service.get_snapshot()
    assert snapshot.stable_presence is PresenceStatus.MULTIPLE
    assert snapshot.stable_posture is PostureStatus.UNKNOWN
    assert snapshot.usable is False
    assert snapshot.reason_codes == (BlockCode.MULTIPLE_PEOPLE,)


async def test_stable_values_ignore_one_detector_error() -> None:
    clock, upper, lower = Clock(), FakeSource(), FakeSource()
    detector = FakeDetector(
        UpperDetection(body_count=1), LowerDetection(1, PostureStatus.STANDING)
    )
    service = make_service(clock, detector, upper, lower)

    for value in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0):
        clock.value = value
        upper.frame = lower.frame = (np.zeros((1, 1)), value)
        await service.process_once()
    assert service.get_snapshot().usable is True

    detector.upper = RuntimeError("transient inference failure")  # type: ignore[assignment]
    clock.value = 3.5
    upper.frame = lower.frame = (np.zeros((1, 1)), 3.5)
    await service.process_once()
    transient = service.get_snapshot()
    assert transient.raw_presence is PresenceStatus.UNKNOWN
    assert transient.stable_presence is PresenceStatus.PRESENT_SINGLE
    assert transient.stable_posture is PostureStatus.STANDING
    assert transient.usable is True
    assert transient.reason_codes == ()

    detector.upper = UpperDetection(body_count=1)
    for value in (4.0, 4.5, 5.0, 5.5, 6.0, 6.5):
        clock.value = value
        upper.frame = lower.frame = (np.zeros((1, 1)), value)
        await service.process_once()
    assert service.get_snapshot().usable is True


class SlowDetector(FakeDetector):
    def detect_upper(self, frame: np.ndarray) -> UpperDetection:
        time.sleep(0.05)
        return super().detect_upper(frame)


async def test_slow_detector_runs_outside_event_loop_and_stop_cleans_task() -> None:
    clock, upper, lower = Clock(), FakeSource(), FakeSource()
    detector = SlowDetector(UpperDetection(body_count=1), LowerDetection(1, PostureStatus.SITTING))
    upper.frame, lower.frame = (np.zeros((1, 1)), 0.0), (np.zeros((1, 1)), 0.0)
    service = make_service(clock, detector, upper, lower)

    running = asyncio.create_task(service.process_once())
    await asyncio.sleep(0.005)
    await asyncio.wait_for(asyncio.sleep(0), timeout=0.01)
    await running
    assert all(identifier != threading.get_ident() for identifier in detector.thread_ids)

    await service.start()
    await service.stop()
    assert service.get_snapshot().usable is False


async def test_lower_rate_limit_uses_latest_frame_without_queueing() -> None:
    clock, upper, lower = Clock(), FakeSource(), FakeSource()
    detector = FakeDetector(
        UpperDetection(body_count=None), LowerDetection(1, PostureStatus.SITTING)
    )
    service = VisionService(
        upper_source=upper,
        lower_source=lower,
        detector=detector,
        settings=VisionSettings(
            lower_inference_interval_seconds=0.5,
            frame_stale_after_seconds=2,
            result_stale_after_seconds=2,
        ),
        monotonic=clock.monotonic,
        utc_now=clock.utc_now,
    )
    for value in (0.0, 0.1, 0.2, 0.5):
        clock.value = value
        lower.frame = (np.full((1, 1), value), value)
        await service.process_once()
    assert detector.lower_calls == 2
    assert service.get_snapshot().lower.captured_monotonic == 0.5


async def test_fresh_lower_raw_posture_is_visible_when_upper_is_unavailable() -> None:
    clock, upper, lower = Clock(), FakeSource(), FakeSource()
    upper.connected = False
    detector = FakeDetector(
        UpperDetection(body_count=None), LowerDetection(1, PostureStatus.SITTING)
    )
    lower.frame = (np.zeros((1, 1)), 0.0)
    service = make_service(clock, detector, upper, lower)
    await service.process_once()
    snapshot = service.get_snapshot()
    assert snapshot.raw_posture is PostureStatus.SITTING
    assert snapshot.stable_posture is PostureStatus.UNKNOWN
    assert snapshot.usable is False


async def test_malformed_lower_result_is_model_error_and_unknown() -> None:
    clock, upper, lower = Clock(), FakeSource(), FakeSource()
    detector = FakeDetector(UpperDetection(body_count=1), LowerDetection(1, PostureStatus.SITTING))
    detector.lower = ValueError("bad shape")  # type: ignore[assignment]
    lower.frame = (np.zeros((1, 1)), 0.0)
    service = make_service(clock, detector, upper, lower)
    await service.process_once()
    snapshot = service.get_snapshot()
    assert BlockCode.MODEL_ERROR in snapshot.reason_codes
    assert snapshot.raw_posture is PostureStatus.UNKNOWN


async def test_multiple_lower_people_still_expose_selected_raw_posture() -> None:
    clock, upper, lower = Clock(), FakeSource(), FakeSource()
    detector = FakeDetector(
        UpperDetection(body_count=None), LowerDetection(2, PostureStatus.SITTING)
    )
    lower.frame = (np.zeros((1, 1)), 0.0)
    service = make_service(clock, detector, upper, lower)
    await service.process_once()
    assert service.get_snapshot().raw_posture is PostureStatus.SITTING
