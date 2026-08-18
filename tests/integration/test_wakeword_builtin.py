"""Voice extra 환경에서 `하이 스마티` ONNX 모델을 검증한다."""

from pathlib import Path

from smart_desk.modules.voice.models import INPUT_FRAME_BYTES
from smart_desk.modules.voice.wakeword import LiveKitWakeWordOnnxDetector


async def test_bundled_model_loads_infers_resets_and_closes() -> None:
    detector = LiveKitWakeWordOnnxDetector(
        model_path=Path("assets/voice/models/hi_smarty_ko_mixed_v0_2_0.onnx"),
        threshold=0.13,
        consecutive_frames=2,
        inference_interval_frames=1,
    )

    await detector.start()
    try:
        for _ in range(26):
            assert await detector.detect(b"\0" * INPUT_FRAME_BYTES) is False
        snapshot = detector.get_debug_snapshot()
        assert snapshot.model == "hi_smarty_ko"
        assert snapshot.score is not None
        detector.reset()
    finally:
        await detector.stop()
