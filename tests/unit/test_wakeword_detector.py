"""공식 openWakeWord HEY_JARVIS ONNX adapter 테스트."""

from types import SimpleNamespace

import numpy as np
import pytest

from smart_desk.modules.voice.models import INPUT_FRAME_BYTES, VoiceFatalError
from smart_desk.modules.voice.wakeword import OpenWakeWordOnnxDetector


class FakeModel:
    def __init__(self, scores: list[float]) -> None:
        self._scores = iter(scores)
        self.processed: list[np.ndarray] = []
        self.reset_count = 0

    def predict(self, samples: np.ndarray) -> dict[str, float]:
        self.processed.append(samples.copy())
        return {"hey_jarvis": next(self._scores)}

    def reset(self) -> None:
        self.reset_count += 1


def install_fake_modules(
    monkeypatch: pytest.MonkeyPatch,
    model: FakeModel,
) -> tuple[list[list[str]], list[dict[str, object]]]:
    downloads: list[list[str]] = []
    constructions: list[dict[str, object]] = []

    def build_model(**kwargs: object) -> FakeModel:
        constructions.append(kwargs)
        return model

    modules = {
        "openwakeword.model": SimpleNamespace(Model=build_model),
        "openwakeword.utils": SimpleNamespace(
            download_models=lambda *, model_names: downloads.append(model_names)
        ),
    }
    monkeypatch.setattr(
        "smart_desk.modules.voice.wakeword.importlib.import_module",
        modules.__getitem__,
    )
    return downloads, constructions


async def test_detector_uses_official_onnx_model_and_consecutive_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeModel([0.0, 0.7, 0.5, 0.9])
    downloads, constructions = install_fake_modules(monkeypatch, model)
    detector = OpenWakeWordOnnxDetector(threshold=0.5, consecutive_frames=2)
    pcm = (np.arange(1_280, dtype="<i2")).tobytes()

    await detector.start()
    assert downloads == [["hey_jarvis"]]
    assert constructions == [
        {"wakeword_models": ["hey_jarvis"], "inference_framework": "onnx"}
    ]
    assert await detector.detect(pcm) is False
    assert await detector.detect(pcm) is False
    snapshot = detector.get_debug_snapshot()
    assert snapshot.score == 0.7
    assert snapshot.activation_streak == 1
    assert snapshot.armed is True
    assert await detector.detect(pcm) is True
    snapshot = detector.get_debug_snapshot()
    assert snapshot.score == 0.5
    assert snapshot.threshold == 0.5
    assert snapshot.activation_streak == 2
    assert snapshot.consecutive_frames == 2
    assert snapshot.armed is False
    assert await detector.detect(pcm) is False
    assert model.processed[0].dtype == np.dtype("int16")
    assert model.processed[0].shape == (1_280,)

    detector.reset()
    assert detector.get_debug_snapshot().score == 0.5
    assert detector.get_debug_snapshot().armed is True
    assert await detector.detect(pcm) is False
    assert model.reset_count == 1
    await detector.stop()
    assert model.reset_count == 2


async def test_detector_rejects_wrong_pcm_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeModel([])
    install_fake_modules(monkeypatch, model)
    detector = OpenWakeWordOnnxDetector(threshold=0.5, consecutive_frames=2)
    await detector.start()

    with pytest.raises(ValueError, match="2560"):
        await detector.detect(b"\0\0")


async def test_model_load_failure_is_content_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modules = {
        "openwakeword.model": SimpleNamespace(
            Model=lambda **_kwargs: (_ for _ in ()).throw(
                RuntimeError("broken model path with secret")
            )
        ),
        "openwakeword.utils": SimpleNamespace(
            download_models=lambda **_kwargs: None
        ),
    }
    monkeypatch.setattr(
        "smart_desk.modules.voice.wakeword.importlib.import_module",
        modules.__getitem__,
    )
    detector = OpenWakeWordOnnxDetector(threshold=0.5, consecutive_frames=2)

    with pytest.raises(VoiceFatalError, match="wakeword_unavailable") as captured:
        await detector.start()

    assert "secret" not in str(captured.value)
