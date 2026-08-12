"""프로젝트의 `하이 스마티` ONNX Wake Word adapter를 구현한다."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import importlib
import math
from pathlib import Path
from typing import Protocol

import numpy as np

from smart_desk.modules.voice.models import INPUT_FRAME_BYTES, VoiceFatalError


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


MODEL_NAME = "hi_smarty_ko"
WINDOW_FRAMES = 25


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


class LiveKitWakeWordOnnxDetector:
    """2초 PCM 창에서 `하이 스마티` 점수와 연속 activation을 관리한다."""

    def __init__(
        self,
        *,
        model_path: Path,
        threshold: float,
        consecutive_frames: int,
    ) -> None:
        self._model_path = model_path
        self._threshold = threshold
        self._consecutive_frames = consecutive_frames
        self._model: object | None = None
        self._frames: deque[np.ndarray] = deque(maxlen=WINDOW_FRAMES)
        self._activation_streak = 0
        self._disarmed = False
        self._last_score: float | None = None

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
        self._activation_streak = 0
        self._disarmed = False
        self._last_score = None

    async def stop(self) -> None:
        self._model = None
        self._frames.clear()
        self._activation_streak = 0
        self._disarmed = False
        self._last_score = None

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

        samples = np.concatenate(tuple(self._frames))
        try:
            score = await asyncio.to_thread(_infer, model, samples)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise VoiceFatalError("wakeword_inference_failed") from error
        self._last_score = score
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
        self._activation_streak = 0
        self._disarmed = False

    def get_debug_snapshot(self) -> WakeWordDebugSnapshot:
        """가장 최근 inference 결과와 activation 조건을 반환한다."""

        return WakeWordDebugSnapshot(
            model=MODEL_NAME,
            score=self._last_score,
            threshold=self._threshold,
            activation_streak=self._activation_streak,
            consecutive_frames=self._consecutive_frames,
            armed=not self._disarmed,
        )
