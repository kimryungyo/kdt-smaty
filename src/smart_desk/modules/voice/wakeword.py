"""openWakeWord 공식 HEY_JARVIS ONNX adapter를 구현한다."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import importlib
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


MODEL_NAME = "hey_jarvis"


def _load_builtin() -> object:
    model_module = importlib.import_module("openwakeword.model")
    utils_module = importlib.import_module("openwakeword.utils")
    utils_module.download_models(model_names=[MODEL_NAME])
    return model_module.Model(
        wakeword_models=[MODEL_NAME],
        inference_framework="onnx",
    )


def _infer(model: object, pcm: bytes) -> float:
    samples = np.frombuffer(pcm, dtype="<i2")
    scores: dict[str, object] = model.predict(samples)  # type: ignore[attr-defined]
    return float(scores.get(MODEL_NAME, 0.0))


class OpenWakeWordOnnxDetector:
    """공식 hey_jarvis ONNX score의 연속 frame activation을 관리한다."""

    def __init__(self, *, threshold: float, consecutive_frames: int) -> None:
        self._threshold = threshold
        self._consecutive_frames = consecutive_frames
        self._model: object | None = None
        self._activation_streak = 0
        self._disarmed = False
        self._last_score: float | None = None

    async def start(self) -> None:
        if self._model is not None:
            return
        try:
            self._model = await asyncio.to_thread(_load_builtin)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise VoiceFatalError("wakeword_unavailable") from error
        self._activation_streak = 0
        self._disarmed = False
        self._last_score = None

    async def stop(self) -> None:
        model, self._model = self._model, None
        self._activation_streak = 0
        self._disarmed = False
        self._last_score = None
        if model is not None:
            try:
                await asyncio.to_thread(model.reset)  # type: ignore[attr-defined]
            except Exception as error:
                raise VoiceFatalError("wakeword_close_failed") from error

    async def detect(self, pcm: bytes) -> bool:
        if len(pcm) != INPUT_FRAME_BYTES:
            raise ValueError(f"Wake Word PCM은 정확히 {INPUT_FRAME_BYTES} bytes여야 합니다.")
        model = self._model
        if model is None:
            raise VoiceFatalError("wakeword_not_started")
        if self._disarmed:
            return False
        try:
            score = await asyncio.to_thread(
                _infer,
                model,
                pcm,
            )
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
        try:
            if self._model is not None:
                self._model.reset()  # type: ignore[attr-defined]
        except Exception as error:
            raise VoiceFatalError("wakeword_reset_failed") from error
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
