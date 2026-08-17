import struct

import pytest

from smart_desk.modules.voice.audio import calculate_rms, analyze_signal_frame
from smart_desk.modules.voice.models import INPUT_FRAME_SAMPLES


def test_signal_diagnostics_and_rms_keep_fixed_pcm_contract():
    pcm = struct.pack("<h", 1000) * INPUT_FRAME_SAMPLES
    assert calculate_rms(pcm) == pytest.approx(1000)
    assert analyze_signal_frame(pcm).peak_dbfs < 0


def test_rms_rejects_non_frame_input():
    with pytest.raises(ValueError): calculate_rms(b"\0\0")
