"""builtin HEY_JARVIS Wake Word adapter 테스트."""

from types import SimpleNamespace

import pytest

from smart_desk.modules.voice.models import INPUT_FRAME_BYTES, VoiceFatalError
from smart_desk.modules.voice.wakeword import PyOpenWakeWordDetector


class FakeFeatures:
    def __init__(self) -> None:
        self.processed: list[bytes] = []
        self.reset_count = 0
        self.close_count = 0

    def process_streaming(self, pcm: bytes):
        self.processed.append(pcm)
        yield b"embedding"

    def reset(self) -> None:
        self.reset_count += 1

    def close(self) -> None:
        self.close_count += 1


class FakeClassifier:
    def __init__(self, scores: list[list[float]]) -> None:
        self._scores = iter(scores)
        self.reset_count = 0
        self.close_count = 0

    def process_streaming(self, _embedding: bytes):
        return iter(next(self._scores))

    def reset(self) -> None:
        self.reset_count += 1

    def close(self) -> None:
        self.close_count += 1


def fake_package(features: FakeFeatures, classifier: FakeClassifier):
    selected: list[object] = []

    class FeatureFactory:
        @classmethod
        def from_builtin(cls):
            return features

    class ClassifierFactory:
        @classmethod
        def from_builtin(cls, model: object):
            selected.append(model)
            return classifier

    package = SimpleNamespace(
        Model=SimpleNamespace(HEY_JARVIS="hey-jarvis-builtin"),
        OpenWakeWordFeatures=FeatureFactory,
        OpenWakeWord=ClassifierFactory,
    )
    return package, selected


async def test_detector_selects_builtin_and_requires_consecutive_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    features = FakeFeatures()
    classifier = FakeClassifier([[], [0.4, 0.7], [0.5], [0.9]])
    package, selected = fake_package(features, classifier)
    monkeypatch.setattr(
        "smart_desk.modules.voice.wakeword.importlib.import_module",
        lambda name: package if name == "pyopen_wakeword" else None,
    )
    detector = PyOpenWakeWordDetector(threshold=0.5, consecutive_frames=2)
    pcm = b"\0" * INPUT_FRAME_BYTES

    await detector.start()
    assert selected == ["hey-jarvis-builtin"]
    assert await detector.detect(pcm) is False  # initial context has no score
    assert await detector.detect(pcm) is False
    assert await detector.detect(pcm) is True
    assert await detector.detect(pcm) is False  # activation 뒤 disarmed

    detector.reset()
    assert await detector.detect(pcm) is False
    assert features.reset_count == 2
    assert classifier.reset_count == 2
    await detector.stop()
    assert features.close_count == 1
    assert classifier.close_count == 1


async def test_detector_rejects_wrong_pcm_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    features = FakeFeatures()
    classifier = FakeClassifier([])
    package, _selected = fake_package(features, classifier)
    monkeypatch.setattr(
        "smart_desk.modules.voice.wakeword.importlib.import_module",
        lambda _name: package,
    )
    detector = PyOpenWakeWordDetector(threshold=0.5, consecutive_frames=2)
    await detector.start()

    with pytest.raises(ValueError, match="2560"):
        await detector.detect(b"\0\0")


async def test_partial_load_failure_closes_features(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    features = FakeFeatures()

    class FeatureFactory:
        @classmethod
        def from_builtin(cls):
            return features

    class BrokenClassifierFactory:
        @classmethod
        def from_builtin(cls, _model: object):
            raise RuntimeError("broken model path with secret")

    package = SimpleNamespace(
        Model=SimpleNamespace(HEY_JARVIS="builtin"),
        OpenWakeWordFeatures=FeatureFactory,
        OpenWakeWord=BrokenClassifierFactory,
    )
    monkeypatch.setattr(
        "smart_desk.modules.voice.wakeword.importlib.import_module",
        lambda _name: package,
    )
    detector = PyOpenWakeWordDetector(threshold=0.5, consecutive_frames=2)

    with pytest.raises(VoiceFatalError, match="wakeword_unavailable") as captured:
        await detector.start()

    assert "secret" not in str(captured.value)
    assert features.close_count == 1
