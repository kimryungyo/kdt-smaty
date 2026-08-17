"""Wake Word 전용 stateful 24kHz→16kHz PCM resampling."""

from __future__ import annotations

import importlib

import numpy as np


class WakeWordResampler:
    """chunk 경계에서 phase를 보존하는 SoXR stream wrapper다."""

    def __init__(self) -> None:
        soxr = importlib.import_module("soxr")
        self._stream = soxr.ResampleStream(24_000, 16_000, 1, dtype="int16", quality="HQ")

    def process(self, pcm: bytes) -> bytes:
        if len(pcm) % 2:
            raise ValueError("PCM16 input은 짝수 bytes여야 합니다.")
        samples = np.frombuffer(pcm, dtype="<i2")
        return np.asarray(self._stream.resample_chunk(samples, last=False), dtype=np.int16).tobytes()

    def finish(self) -> bytes:
        """현재 발화의 stream을 flush한다. 다음 발화에는 새 instance를 쓴다."""
        return np.asarray(
            self._stream.resample_chunk(np.empty(0, dtype=np.int16), last=True), dtype=np.int16
        ).tobytes()
