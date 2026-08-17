from __future__ import annotations

import numpy as np
import pytest

from smart_desk.modules.voice.resample import WakeWordResampler


def test_soxr_resampler_is_stateful_and_keeps_24khz_source_unchanged() -> None:
    source_samples = np.arange(24_000 * 3, dtype=np.int16)
    source = source_samples.tobytes()
    resampler = WakeWordResampler()  # actual optional native soxr import

    converted = b"".join(
        resampler.process(source[index:index + 1_920 * 2])
        for index in range(0, len(source), 1_920 * 2)
    ) + resampler.finish()

    assert len(converted) // 2 == 16_000 * 3
    assert source == source_samples.tobytes()


def test_soxr_resampler_rejects_odd_pcm16_bytes() -> None:
    with pytest.raises(ValueError, match="짝수"):
        WakeWordResampler().process(b"\0")
