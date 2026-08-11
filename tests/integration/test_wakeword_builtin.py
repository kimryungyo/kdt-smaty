"""Voice extra 환경에서 공식 HEY_JARVIS ONNX 모델을 검증한다."""

from smart_desk.modules.voice.models import INPUT_FRAME_BYTES
from smart_desk.modules.voice.wakeword import OpenWakeWordOnnxDetector


async def test_builtin_model_loads_infers_resets_and_closes() -> None:
    detector = OpenWakeWordOnnxDetector(threshold=0.5, consecutive_frames=2)

    await detector.start()
    try:
        for _ in range(5):
            assert await detector.detect(b"\0" * INPUT_FRAME_BYTES) is False
        detector.reset()
    finally:
        await detector.stop()
