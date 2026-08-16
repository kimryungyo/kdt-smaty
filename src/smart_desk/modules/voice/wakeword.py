"""프로젝트의 `하이 스마티` ONNX Wake Word adapter를 구현한다."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import importlib
import math
from pathlib import Path
import time
from typing import Protocol

import numpy as np

from smart_desk.modules.voice.models import (
    INPUT_FRAME_BYTES,
    INPUT_SAMPLE_RATE,
    VoiceFatalError,
)


class WakeWordDetector(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def detect(self, pcm: bytes) -> bool: ...

    def reset(self) -> None: ...


@dataclass(frozen=True, slots=True)
class WakeWordDebugSnapshot:
    """Wake Word classifier의 content-free 실시간 관측값이다."""

    model: str
    score: float | None
    threshold: float
    activation_streak: int
    consecutive_frames: int
    armed: bool
    recent_max_score: float | None
    inference_count: int
    last_inference_ms: float | None
    inference_p50_ms: float | None
    inference_p95_ms: float | None


MODEL_NAME = "hi_smarty_ko"
MODEL_SAMPLE_RATE = 16_000
WINDOW_FRAMES = 25
MODEL_WINDOW_SAMPLES = MODEL_SAMPLE_RATE * 2
RECENT_SCORE_WINDOW_SECONDS = 30.0
INFERENCE_LATENCY_SAMPLES = 256


def _load_model(model_path: Path) -> object:
    wakeword_module = importlib.import_module("livekit.wakeword")
    model = wakeword_module.WakeWordModel()
    model.load_model(model_path, model_name=MODEL_NAME)
    return model


def _infer(model: object, samples: np.ndarray) -> float:
    scores: dict[str, object] = model.predict(samples)  # type: ignore[attr-defined]
    score = float(scores[MODEL_NAME])
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError("invalid wakeword score")
    return score


def _resample_for_model(samples: np.ndarray) -> np.ndarray:
    """24kHz microphone window를 Wake Word 모델의 16kHz 계약으로 변환한다."""

    soxr = importlib.import_module("soxr")
    resampled = soxr.resample(  # type: ignore[attr-defined]
        samples,
        INPUT_SAMPLE_RATE,
        MODEL_SAMPLE_RATE,
        quality="HQ",
    )
    if resampled.dtype != np.dtype("int16") or resampled.shape != (
        MODEL_WINDOW_SAMPLES,
    ):
        raise ValueError("invalid wakeword resampling result")
    return resampled


def _timed_infer(model: object, samples: np.ndarray) -> tuple[float, float]:
    """리샘플링과 모델 추론의 총 실행 시간을 밀리초 단위로 반환한다."""

    started_at = time.perf_counter()
    score = _infer(model, _resample_for_model(samples))
    return score, (time.perf_counter() - started_at) * 1_000


class LiveKitWakeWordOnnxDetector:
    """2초 PCM 창에서 `하이 스마티` 점수와 연속 activation을 관리한다."""

    def __init__(
        self,
        *,
        model_path: Path,
        threshold: float,
        consecutive_frames: int,
        inference_interval_frames: int,
    ) -> None:
        self._model_path = model_path
        self._threshold = threshold
        self._consecutive_frames = consecutive_frames
        self._inference_interval_frames = inference_interval_frames
        self._model: object | None = None
        self._frames: deque[np.ndarray] = deque(maxlen=WINDOW_FRAMES)
        self._frames_since_inference = inference_interval_frames - 1
        self._activation_streak = 0
        self._disarmed = False
        self._last_score: float | None = None
        self._recent_scores: deque[tuple[float, float]] = deque()
        self._inference_latencies_ms: deque[float] = deque(
            maxlen=INFERENCE_LATENCY_SAMPLES
        )
        self._inference_count = 0
        self._last_inference_ms: float | None = None

    async def start(self) -> None:
        if self._model is not None:
            return
        try:
            self._model = await asyncio.to_thread(_load_model, self._model_path)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise VoiceFatalError("wakeword_unavailable") from error
        self._frames.clear()
        self._frames_since_inference = self._inference_interval_frames - 1
        self._activation_streak = 0
        self._disarmed = False
        self._last_score = None
        self._recent_scores.clear()
        self._inference_latencies_ms.clear()
        self._inference_count = 0
        self._last_inference_ms = None

    async def stop(self) -> None:
        self._model = None
        self._frames.clear()
        self._frames_since_inference = self._inference_interval_frames - 1
        self._activation_streak = 0
        self._disarmed = False
        self._last_score = None
        self._recent_scores.clear()
        self._inference_latencies_ms.clear()
        self._inference_count = 0
        self._last_inference_ms = None

    async def detect(self, pcm: bytes) -> bool:
        if len(pcm) != INPUT_FRAME_BYTES:
            raise ValueError(f"Wake Word PCM은 정확히 {INPUT_FRAME_BYTES} bytes여야 합니다.")
        model = self._model
        if model is None:
            raise VoiceFatalError("wakeword_not_started")
        if self._disarmed:
            return False

        self._frames.append(np.frombuffer(pcm, dtype="<i2").copy())
        if len(self._frames) < WINDOW_FRAMES:
            return False
        self._frames_since_inference += 1
        inference_interval = (
            1 if self._activation_streak else self._inference_interval_frames
        )
        if self._frames_since_inference < inference_interval:
            return False
        self._frames_since_inference = 0

        samples = np.concatenate(tuple(self._frames))
        try:
            score, inference_ms = await asyncio.to_thread(_timed_infer, model, samples)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise VoiceFatalError("wakeword_inference_failed") from error
        self._last_score = score
        self._last_inference_ms = inference_ms
        self._inference_latencies_ms.append(inference_ms)
        self._inference_count += 1
        self._recent_scores.append((time.monotonic(), score))
        self._discard_expired_scores()
        if score < self._threshold:
            self._activation_streak = 0
            return False
        self._activation_streak += 1
        if self._activation_streak < self._consecutive_frames:
            return False
        self._disarmed = True
        return True

    def reset(self) -> None:
        self._frames.clear()
        self._frames_since_inference = self._inference_interval_frames - 1
        self._activation_streak = 0
        self._disarmed = False
        self._recent_scores.clear()

    def _discard_expired_scores(self) -> None:
        before = time.monotonic() - RECENT_SCORE_WINDOW_SECONDS
        while self._recent_scores and self._recent_scores[0][0] < before:
            self._recent_scores.popleft()

    def get_debug_snapshot(self) -> WakeWordDebugSnapshot:
        """가장 최근 inference 결과와 activation 조건을 반환한다."""

        self._discard_expired_scores()
        latencies = tuple(self._inference_latencies_ms)
        return WakeWordDebugSnapshot(
            model=MODEL_NAME,
            score=self._last_score,
            threshold=self._threshold,
            activation_streak=self._activation_streak,
            consecutive_frames=self._consecutive_frames,
            armed=not self._disarmed,
            recent_max_score=(
                None
                if not self._recent_scores
                else max(score for _, score in self._recent_scores)
            ),
            inference_count=self._inference_count,
            last_inference_ms=self._last_inference_ms,
            inference_p50_ms=(
                None if not latencies else float(np.percentile(latencies, 50))
            ),
            inference_p95_ms=(
                None if not latencies else float(np.percentile(latencies, 95))
            ),
        )
