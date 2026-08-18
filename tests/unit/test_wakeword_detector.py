"""`하이 스마티` livekit-wakeword ONNX adapter 테스트."""

import importlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from smart_desk.modules.voice.models import (
    INPUT_FRAME_BYTES,
    INPUT_FRAME_SAMPLES,
    VoiceFatalError,
)
from smart_desk.modules.voice.wakeword import (
    LiveKitWakeWordOnnxDetector,
    MODEL_WINDOW_SAMPLES,
    WINDOW_FRAMES,
)


MODEL_PATH = Path("assets/voice/models/hi_smarty_ko_mixed_v0_2_0.onnx")


class FakeModel:
    def __init__(self, scores: list[float]) -> None:
        self._scores = iter(scores)
        self.load_calls: list[tuple[Path, str]] = []
        self.processed: list[np.ndarray] = []

    def load_model(self, model_path: Path, *, model_name: str) -> None:
        self.load_calls.append((model_path, model_name))

    def predict(self, samples: np.ndarray) -> dict[str, float]:
        self.processed.append(samples.copy())
        return {"hi_smarty_ko": next(self._scores)}


def install_fake_module(monkeypatch: pytest.MonkeyPatch, model: FakeModel) -> None:
    module = SimpleNamespace(WakeWordModel=lambda: model)
    import_module = importlib.import_module
    monkeypatch.setattr(
        "smart_desk.modules.voice.wakeword.importlib.import_module",
        lambda name: module if name == "livekit.wakeword" else import_module(name),
    )


async def feed_frames(
    detector: LiveKitWakeWordOnnxDetector,
    count: int,
    *,
    value: int = 0,
) -> list[bool]:
    pcm = np.full(INPUT_FRAME_SAMPLES, value, dtype="<i2").tobytes()
    return [await detector.detect(pcm) for _ in range(count)]


async def test_detector_uses_two_second_rolling_window_and_consecutive_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeModel([0.0, 0.7, 0.5, 0.9])
    install_fake_module(monkeypatch, model)
    detector = LiveKitWakeWordOnnxDetector(
        model_path=MODEL_PATH,
        threshold=0.5,
        consecutive_frames=2,
        inference_interval_frames=1,
    )

    await detector.start()
    assert model.load_calls == [(MODEL_PATH, "hi_smarty_ko")]
    assert await feed_frames(detector, WINDOW_FRAMES - 1) == [False] * 24
    assert model.processed == []
    assert await feed_frames(detector, 1) == [False]
    assert await feed_frames(detector, 1) == [False]
    snapshot = detector.get_debug_snapshot()
    assert snapshot.score == 0.7
    assert snapshot.activation_streak == 1
    assert snapshot.armed is True
    assert await feed_frames(detector, 1) == [True]
    snapshot = detector.get_debug_snapshot()
    assert snapshot.score == 0.5
    assert snapshot.threshold == 0.5
    assert snapshot.activation_streak == 2
    assert snapshot.consecutive_frames == 2
    assert snapshot.armed is False
    assert await feed_frames(detector, 1) == [False]
    assert model.processed[0].dtype == np.dtype("int16")
    assert model.processed[0].shape == (MODEL_WINDOW_SAMPLES,)

    detector.reset()
    assert detector.get_debug_snapshot().score == 0.5
    assert detector.get_debug_snapshot().armed is True
    assert await feed_frames(detector, WINDOW_FRAMES - 1) == [False] * 24
    assert len(model.processed) == 3
    assert await feed_frames(detector, 1) == [False]
    assert len(model.processed) == 4
    await detector.stop()
    assert detector.get_debug_snapshot().score is None


async def test_detector_rejects_wrong_pcm_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeModel([])
    install_fake_module(monkeypatch, model)
    detector = LiveKitWakeWordOnnxDetector(
        model_path=MODEL_PATH,
        threshold=0.13,
        consecutive_frames=2,
        inference_interval_frames=1,
    )
    await detector.start()

    with pytest.raises(ValueError, match=str(INPUT_FRAME_BYTES)):
        await detector.detect(b"\0\0")


async def test_model_load_failure_is_content_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = SimpleNamespace(
        WakeWordModel=lambda: (_ for _ in ()).throw(
            RuntimeError("broken model path with secret")
        )
    )
    monkeypatch.setattr(
        "smart_desk.modules.voice.wakeword.importlib.import_module",
        lambda _name: module,
    )
    detector = LiveKitWakeWordOnnxDetector(
        model_path=MODEL_PATH,
        threshold=0.13,
        consecutive_frames=2,
        inference_interval_frames=1,
    )

    with pytest.raises(VoiceFatalError, match="wakeword_unavailable") as captured:
        await detector.start()

    assert "secret" not in str(captured.value)


async def test_detector_limits_inference_frequency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeModel([0.1, 0.2])
    install_fake_module(monkeypatch, model)
    detector = LiveKitWakeWordOnnxDetector(
        model_path=MODEL_PATH,
        threshold=0.5,
        consecutive_frames=2,
        inference_interval_frames=5,
    )

    await detector.start()
    await feed_frames(detector, WINDOW_FRAMES)
    assert len(model.processed) == 1

    await feed_frames(detector, 4)
    assert len(model.processed) == 1

    await feed_frames(detector, 1)
    assert len(model.processed) == 2


async def test_detector_confirms_positive_score_on_next_audio_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeModel([0.6, 0.7])
    install_fake_module(monkeypatch, model)
    detector = LiveKitWakeWordOnnxDetector(
        model_path=MODEL_PATH,
        threshold=0.5,
        consecutive_frames=2,
        inference_interval_frames=5,
    )

    await detector.start()
    assert await feed_frames(detector, WINDOW_FRAMES) == [False] * WINDOW_FRAMES
    assert detector.get_debug_snapshot().activation_streak == 1

    assert await feed_frames(detector, 1) == [True]
    assert len(model.processed) == 2


async def test_detector_reports_recent_score_and_inference_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeModel([0.1, 0.7, 0.5])
    install_fake_module(monkeypatch, model)
    timings = iter([(0.1, 10.0), (0.7, 20.0), (0.5, 30.0)])
    monkeypatch.setattr(
        "smart_desk.modules.voice.wakeword._timed_infer",
        lambda _model, _samples: next(timings),
    )
    detector = LiveKitWakeWordOnnxDetector(
        model_path=MODEL_PATH,
        threshold=0.9,
        consecutive_frames=2,
        inference_interval_frames=1,
    )

    await detector.start()
    await feed_frames(detector, WINDOW_FRAMES + 2)
    snapshot = detector.get_debug_snapshot()

    assert snapshot.recent_max_score == 0.7
    assert snapshot.inference_count == 3
    assert snapshot.last_inference_ms == 30.0
    assert snapshot.inference_p50_ms == 20.0
    assert snapshot.inference_p95_ms == pytest.approx(29.0)

    detector.reset()
    reset_snapshot = detector.get_debug_snapshot()
    assert reset_snapshot.score == 0.5
    assert reset_snapshot.recent_max_score is None
    assert reset_snapshot.inference_count == 3
    assert reset_snapshot.inference_p50_ms == 20.0


async def test_detector_expires_recent_scores_after_thirty_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeModel([0.4])
    install_fake_module(monkeypatch, model)
    now = [100.0]
    monkeypatch.setattr(
        "smart_desk.modules.voice.wakeword.time.monotonic", lambda: now[0]
    )
    detector = LiveKitWakeWordOnnxDetector(
        model_path=MODEL_PATH,
        threshold=0.9,
        consecutive_frames=2,
        inference_interval_frames=1,
    )

    await detector.start()
    await feed_frames(detector, WINDOW_FRAMES)
    assert detector.get_debug_snapshot().recent_max_score == 0.4

    now[0] += 30.01
    assert detector.get_debug_snapshot().recent_max_score is None
