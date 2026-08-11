"""pyopen-wakeword builtin HEY_JARVIS adapter를 구현한다."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
import importlib
from typing import Protocol

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


def _load_builtin() -> tuple[object, object]:
    features: object | None = None
    try:
        package = importlib.import_module("pyopen_wakeword")
        features = package.OpenWakeWordFeatures.from_builtin()
        classifier = package.OpenWakeWord.from_builtin(package.Model.HEY_JARVIS)
        features.reset()
        classifier.reset()
        return features, classifier
    except Exception:
        if features is not None:
            try:
                features.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        raise


def _infer(features: object, classifier: object, pcm: bytes) -> float | None:
    scores: list[float] = []
    embeddings: Iterable[object] = features.process_streaming(pcm)  # type: ignore[attr-defined]
    for embedding in embeddings:
        probabilities: Iterable[object] = classifier.process_streaming(  # type: ignore[attr-defined]
            embedding
        )
        for probability in probabilities:
            scores.append(float(probability))
    return max(scores) if scores else None


class PyOpenWakeWordDetector:
    """package 내장 hey_jarvis classifier의 연속 frame activation을 관리한다."""

    def __init__(self, *, threshold: float, consecutive_frames: int) -> None:
        self._threshold = threshold
        self._consecutive_frames = consecutive_frames
        self._features: object | None = None
        self._classifier: object | None = None
        self._activation_streak = 0
        self._disarmed = False
        self._last_score: float | None = None

    async def start(self) -> None:
        if self._features is not None or self._classifier is not None:
            return
        try:
            self._features, self._classifier = await asyncio.to_thread(_load_builtin)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise VoiceFatalError("wakeword_unavailable") from error
        self._activation_streak = 0
        self._disarmed = False
        self._last_score = None

    async def stop(self) -> None:
        classifier, self._classifier = self._classifier, None
        features, self._features = self._features, None
        self._activation_streak = 0
        self._disarmed = False
        self._last_score = None
        errors: list[BaseException] = []
        for resource in (classifier, features):
            if resource is None:
                continue
            try:
                await asyncio.to_thread(resource.close)  # type: ignore[attr-defined]
            except Exception as error:
                errors.append(error)
        if errors:
            raise VoiceFatalError("wakeword_close_failed") from errors[0]

    async def detect(self, pcm: bytes) -> bool:
        if len(pcm) != INPUT_FRAME_BYTES:
            raise ValueError(f"Wake Word PCM은 정확히 {INPUT_FRAME_BYTES} bytes여야 합니다.")
        if self._features is None or self._classifier is None:
            raise VoiceFatalError("wakeword_not_started")
        if self._disarmed:
            return False
        try:
            score = await asyncio.to_thread(
                _infer,
                self._features,
                self._classifier,
                pcm,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise VoiceFatalError("wakeword_inference_failed") from error
        self._last_score = score
        if score is None or score < self._threshold:
            self._activation_streak = 0
            return False
        self._activation_streak += 1
        if self._activation_streak < self._consecutive_frames:
            return False
        self._disarmed = True
        return True

    def reset(self) -> None:
        try:
            if self._features is not None:
                self._features.reset()  # type: ignore[attr-defined]
            if self._classifier is not None:
                self._classifier.reset()  # type: ignore[attr-defined]
        except Exception as error:
            raise VoiceFatalError("wakeword_reset_failed") from error
        self._activation_streak = 0
        self._disarmed = False

    def get_debug_snapshot(self) -> WakeWordDebugSnapshot:
        """가장 최근 inference 결과와 activation 조건을 반환한다."""

        return WakeWordDebugSnapshot(
            model="hey_jarvis",
            score=self._last_score,
            threshold=self._threshold,
            activation_streak=self._activation_streak,
            consecutive_frames=self._consecutive_frames,
            armed=not self._disarmed,
        )
