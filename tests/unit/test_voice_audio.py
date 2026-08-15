"""PCM queue, RMS recorder와 memory WAV 테스트."""

import asyncio
from collections import deque
import math
import struct
import time
import wave
from io import BytesIO

import numpy as np
import pytest

from smart_desk.modules.voice.audio import (
    AudioSignalFrame,
    LocalAudioInput,
    RmsRecorder,
    _refresh_portaudio_devices,
    _to_device_pcm,
    analyze_signal_frame,
    build_wav,
    calculate_rms,
)
from smart_desk.modules.voice.models import (
    AudioChunk,
    INPUT_FRAME_BYTES,
    RecordingEnd,
    VoiceFatalError,
)


def pcm_frame(value: int) -> bytes:
    return struct.pack("<h", value) * (INPUT_FRAME_BYTES // 2)


class FakeAudioInput:
    def __init__(self, chunks: list[AudioChunk]) -> None:
        self._chunks = deque(chunks)

    async def read(self, timeout_seconds: float | None = None) -> AudioChunk:
        if not self._chunks:
            if timeout_seconds is not None:
                raise TimeoutError
            await asyncio.Future()
        return self._chunks.popleft()


def test_calculate_rms_uses_signed_int16_without_overflow() -> None:
    assert calculate_rms(pcm_frame(1_000)) == pytest.approx(1_000)
    assert calculate_rms(pcm_frame(-32_768)) == pytest.approx(32_768)


def test_analyze_signal_frame_reports_finite_silence_and_pcm_levels() -> None:
    silence = analyze_signal_frame(pcm_frame(0))
    symmetric = analyze_signal_frame(
        struct.pack("<1280h", *([1_000, -1_000] * 640))
    )
    constant = analyze_signal_frame(pcm_frame(1_000))

    assert silence.rms_dbfs == -120.0
    assert silence.peak_dbfs == -120.0
    assert silence.clipping_ratio == 0
    assert silence.dc_offset_pcm == 0
    assert symmetric.rms_dbfs == pytest.approx(-30.3089987)
    assert symmetric.peak_dbfs == pytest.approx(-30.3089987)
    assert symmetric.dc_offset_pcm == 0
    assert constant.dc_offset_pcm == 1_000


def test_analyze_signal_frame_detects_pcm_rails_without_overflow() -> None:
    samples = [-32_768] * 5 + [0] * (1_280 - 5)
    signal = analyze_signal_frame(struct.pack("<1280h", *samples))

    assert signal.peak_dbfs == pytest.approx(0.0)
    assert signal.clipping_ratio == pytest.approx(5 / 1_280)
    assert math.isfinite(signal.rms_dbfs)


def test_build_wav_has_expected_header_format_and_duration() -> None:
    utterance = build_wav([pcm_frame(100), pcm_frame(200)])

    with wave.open(BytesIO(utterance.wav), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16_000
        assert wav_file.getnframes() == 2_560
    assert utterance.duration_seconds == pytest.approx(0.16)


def test_output_pcm_is_upsampled_and_duplicated_to_stereo() -> None:
    converted = np.frombuffer(
        _to_device_pcm(struct.pack("<hh", 100, -200)),
        dtype="<i2",
    ).reshape(-1, 2)

    assert converted.tolist() == [
        [100, 100],
        [100, 100],
        [-200, -200],
        [-200, -200],
    ]


def test_portaudio_device_refresh_reinitializes_runtime() -> None:
    calls: list[str] = []

    class SoundDevice:
        @staticmethod
        def _terminate() -> None:
            calls.append("terminate")

        @staticmethod
        def _initialize() -> None:
            calls.append("initialize")

    _refresh_portaudio_devices(SoundDevice())

    assert calls == ["terminate", "initialize"]


async def test_input_queue_drops_oldest_and_rejects_stale_generation() -> None:
    audio = LocalAudioInput(device_name=None, queue_frames=2)
    audio.set_accepting(True)
    generation = audio._generation  # noqa: SLF001

    for timestamp in (1.0, 2.0, 3.0):
        audio._enqueue_from_loop(  # noqa: SLF001
            generation,
            pcm_frame(int(timestamp)),
            timestamp,
            False,
            False,
        )

    assert (await audio.read()).captured_at == 2.0
    assert (await audio.read()).captured_at == 3.0
    assert audio._dropped_frames == 1  # noqa: SLF001
    snapshot = audio.get_debug_snapshot()
    assert snapshot.accepting is True
    assert snapshot.queue_size == 0
    assert snapshot.queue_capacity == 2
    assert snapshot.dropped_frames == 1
    assert snapshot.overflow_frames == 0
    assert snapshot.callback_errors == 0

    audio._enqueue_from_loop(  # noqa: SLF001
        generation,
        pcm_frame(4),
        4.0,
        False,
        False,
    )
    audio.discard_pending()
    audio._enqueue_from_loop(  # noqa: SLF001
        generation,
        pcm_frame(5),
        5.0,
        False,
        False,
    )
    assert audio._queue.empty()  # noqa: SLF001


def test_input_signal_history_uses_noise_percentile_and_time_windows() -> None:
    audio = LocalAudioInput(device_name=None, queue_frames=2)

    audio._record_signal_frame(  # noqa: SLF001
        AudioSignalFrame(-80, -1, 0, 0), 0.0
    )
    audio._record_signal_frame(  # noqa: SLF001
        AudioSignalFrame(-60, -20, 0, 0), 1.0
    )
    audio._record_signal_frame(  # noqa: SLF001
        AudioSignalFrame(-40, -10, 0, 0), 2.0
    )
    snapshot = audio.get_debug_snapshot()

    assert snapshot.estimated_noise_floor_dbfs == pytest.approx(-72.0)
    assert snapshot.estimated_snr_db == pytest.approx(32.0)
    assert snapshot.recent_peak_dbfs == -1

    audio._record_signal_frame(  # noqa: SLF001
        AudioSignalFrame(-50, -25, 0, 0), 10.01
    )
    assert audio.get_debug_snapshot().recent_peak_dbfs == -10

    audio._record_signal_frame(  # noqa: SLF001
        AudioSignalFrame(-70, -30, 0, 0), 30.01
    )
    snapshot = audio.get_debug_snapshot()
    assert snapshot.estimated_noise_floor_dbfs == pytest.approx(-64.0)
    assert snapshot.recent_peak_dbfs == -30


async def test_input_stop_clears_stale_signal_snapshot() -> None:
    audio = LocalAudioInput(device_name=None, queue_frames=2)
    audio.set_accepting(True)
    audio._enqueue_from_loop(  # noqa: SLF001
        audio._generation,  # noqa: SLF001
        pcm_frame(1_000),
        1.0,
        False,
        False,
    )
    await audio.stop()

    snapshot = audio.get_debug_snapshot()
    assert snapshot.latest_rms_dbfs is None
    assert snapshot.estimated_noise_floor_dbfs is None
    assert snapshot.latest_clipping_ratio is None
    assert snapshot.clipped_frames == 0
    assert snapshot.signal_frames == 0


async def test_callback_moves_owned_pcm_to_event_loop_queue() -> None:
    audio = LocalAudioInput(device_name=None, queue_frames=2)
    audio._loop = asyncio.get_running_loop()  # noqa: SLF001
    audio.set_accepting(True)

    class Status:
        input_overflow = False

        def __bool__(self) -> bool:
            return False

    audio._callback(memoryview(pcm_frame(321)), 1_280, object(), Status())  # noqa: SLF001
    await asyncio.sleep(0)

    chunk = await audio.read(timeout_seconds=0.1)
    assert chunk.pcm == pcm_frame(321)
    assert chunk.captured_at > 0


async def test_input_detects_stalled_callback_while_stream_reports_active() -> None:
    audio = LocalAudioInput(device_name=None, queue_frames=2)
    audio._stream = type("Stream", (), {"active": True})()  # noqa: SLF001
    audio._last_callback_at = time.monotonic() - 2  # noqa: SLF001

    with pytest.raises(VoiceFatalError, match="microphone_inactive"):
        await audio.read(timeout_seconds=0.001)


async def test_input_keeps_normal_timeout_for_recent_callback() -> None:
    audio = LocalAudioInput(device_name=None, queue_frames=2)
    audio._stream = type("Stream", (), {"active": True})()  # noqa: SLF001
    audio._last_callback_at = time.monotonic()  # noqa: SLF001

    with pytest.raises(TimeoutError):
        await audio.read(timeout_seconds=0.001)


async def test_recorder_uses_two_frame_start_preroll_and_silence_end() -> None:
    chunks: list[AudioChunk] = []
    for index, value in enumerate([0, 0, 900, 900, 900, *([0] * 8)]):
        chunks.append(AudioChunk(pcm=pcm_frame(value), captured_at=index * 0.08))
    recorder = RmsRecorder(
        rms_threshold=500,
        speech_start_consecutive_frames=2,
        silence_duration_seconds=0.6,
        min_utterance_seconds=0.24,
        max_utterance_seconds=10,
        preroll_seconds=0.3,
    )

    utterance, end = await recorder.record(
        FakeAudioInput(chunks),  # type: ignore[arg-type]
        speech_start_deadline=time.monotonic() + 1,
    )

    assert end is RecordingEnd.SILENCE
    assert utterance is not None
    assert utterance.duration_seconds == pytest.approx(1.04)
    with wave.open(BytesIO(utterance.wav), "rb") as wav_file:
        first_samples = wav_file.readframes(2_560)
    assert first_samples.startswith(pcm_frame(0))


async def test_recorder_rejects_too_short_utterance() -> None:
    chunks = [
        AudioChunk(pcm=pcm_frame(value), captured_at=index * 0.08)
        for index, value in enumerate([900, 900, *([0] * 8)])
    ]
    recorder = RmsRecorder(
        rms_threshold=500,
        speech_start_consecutive_frames=2,
        silence_duration_seconds=0.6,
        min_utterance_seconds=0.24,
        max_utterance_seconds=10,
        preroll_seconds=0.3,
    )

    utterance, end = await recorder.record(
        FakeAudioInput(chunks),  # type: ignore[arg-type]
        speech_start_deadline=time.monotonic() + 1,
    )

    assert utterance is None
    assert end is RecordingEnd.TOO_SHORT


async def test_recorder_times_out_before_speech() -> None:
    recorder = RmsRecorder(
        rms_threshold=500,
        speech_start_consecutive_frames=2,
        silence_duration_seconds=0.6,
        min_utterance_seconds=0.24,
        max_utterance_seconds=10,
        preroll_seconds=0.3,
    )

    utterance, end = await recorder.record(
        FakeAudioInput([]),  # type: ignore[arg-type]
        speech_start_deadline=time.monotonic() + 0.01,
    )

    assert utterance is None
    assert end is RecordingEnd.SPEECH_START_TIMEOUT


def test_pcm_validation_rejects_wrong_frame_size() -> None:
    with pytest.raises(ValueError):
        calculate_rms(b"\0\0")
    with pytest.raises(ValueError):
        build_wav([b"\0\0"])
    assert math.isfinite(calculate_rms(pcm_frame(32_767)))
