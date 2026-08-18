"""로컬 PCM 입출력, RMS 발화 녹음과 memory WAV 조립을 구현한다."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import importlib
import logging
from math import ceil, log10
import time
from typing import Protocol

import numpy as np

from smart_desk.modules.voice.models import (
    AudioChunk,
    INPUT_FRAME_BYTES,
    INPUT_FRAME_SAMPLES,
    INPUT_FRAME_SECONDS,
    INPUT_SAMPLE_RATE,
    OUTPUT_SAMPLE_RATE,
    VoiceFatalError,
)


LOGGER = logging.getLogger(__name__)
OUTPUT_DEVICE_SAMPLE_RATE = 48_000
INPUT_CALLBACK_STALE_SECONDS = 1.0
FALLBACK_INPUT_SAMPLE_RATE = 48_000
SIGNAL_DBFS_FLOOR = -120.0
SIGNAL_CLIPPING_SAMPLE = 32_760
SIGNAL_NOISE_WINDOW_SECONDS = 30.0
SIGNAL_RECENT_PEAK_WINDOW_SECONDS = 10.0
SIGNAL_NOISE_WINDOW_FRAMES = ceil(SIGNAL_NOISE_WINDOW_SECONDS / INPUT_FRAME_SECONDS)
SIGNAL_RECENT_PEAK_WINDOW_FRAMES = ceil(
    SIGNAL_RECENT_PEAK_WINDOW_SECONDS / INPUT_FRAME_SECONDS
)


@dataclass(frozen=True, slots=True)
class AudioSignalFrame:
    """원본 PCM을 보관하지 않는 단일 입력 frame의 신호 통계다."""

    rms_dbfs: float
    peak_dbfs: float
    clipping_ratio: float
    dc_offset_pcm: float


@dataclass(frozen=True, slots=True)
class AudioInputDebugSnapshot:
    """원본 PCM을 제외한 microphone queue 관측값이다."""

    accepting: bool
    queue_size: int
    queue_capacity: int
    dropped_frames: int
    overflow_frames: int
    callback_errors: int
    latest_rms_dbfs: float | None
    latest_peak_dbfs: float | None
    recent_peak_dbfs: float | None
    estimated_noise_floor_dbfs: float | None
    estimated_snr_db: float | None
    latest_clipping_ratio: float | None
    clipped_frames: int
    signal_frames: int
    latest_dc_offset_pcm: float | None


class AudioInput(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def read(self, timeout_seconds: float | None = None) -> AudioChunk: ...

    def set_accepting(self, enabled: bool) -> None: ...

    def discard_pending(self) -> None: ...


class PcmOutput(Protocol):
    async def start(self) -> None: ...

    async def write(self, pcm: bytes) -> None: ...

    async def drain(self) -> None: ...

    async def abort(self) -> None: ...

    async def stop(self) -> None: ...


def calculate_rms(pcm: bytes) -> float:
    """고정 input frame의 RMS를 int16 overflow 없이 계산한다."""

    if len(pcm) != INPUT_FRAME_BYTES:
        raise ValueError(f"입력 PCM은 정확히 {INPUT_FRAME_BYTES} bytes여야 합니다.")
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    return float(np.sqrt(np.mean(samples * samples)))


def analyze_signal_frame(pcm: bytes) -> AudioSignalFrame:
    """PCM16 frame을 content-free 입력 품질 수치로 변환한다."""

    if len(pcm) != INPUT_FRAME_BYTES:
        raise ValueError(f"입력 PCM은 정확히 {INPUT_FRAME_BYTES} bytes여야 합니다.")
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    rms = float(np.sqrt(np.mean(samples * samples)))
    peak = float(np.max(np.abs(samples)))

    def dbfs(amplitude: float) -> float:
        if amplitude <= 0:
            return SIGNAL_DBFS_FLOOR
        return max(SIGNAL_DBFS_FLOOR, min(0.0, 20.0 * log10(amplitude / 32_768)))

    return AudioSignalFrame(
        rms_dbfs=dbfs(rms),
        peak_dbfs=dbfs(peak),
        clipping_ratio=float(np.mean(np.abs(samples) >= SIGNAL_CLIPPING_SAMPLE)),
        dc_offset_pcm=float(np.mean(samples)),
    )



def _resolve_device_index(
    sounddevice: object,
    *,
    name: str | None,
    input_device: bool,
) -> int | None:
    devices = sounddevice.query_devices()  # type: ignore[attr-defined]
    if name is None:
        default = sounddevice.default.device  # type: ignore[attr-defined]
        index = default[0 if input_device else 1]
        return None if index is None or int(index) < 0 else int(index)

    normalized = name.strip().casefold()
    matches = [
        index
        for index, device in enumerate(devices)
        if str(device["name"]).strip().casefold() == normalized
    ]
    if len(matches) != 1:
        kind = "input" if input_device else "output"
        raise VoiceFatalError(f"{kind}_device_name_invalid")
    return matches[0]


def _refresh_portaudio_devices(sounddevice: object) -> None:
    """닫힌 audio runtime의 PortAudio 장치 목록을 hot-plug 이후 갱신한다."""

    terminate = getattr(sounddevice, "_terminate", None)
    initialize = getattr(sounddevice, "_initialize", None)
    if not callable(terminate) or not callable(initialize):
        return
    terminate()
    initialize()


class LocalAudioInput:
    """PortAudio callback PCM을 event-loop-owned bounded queue로 전달한다."""

    def __init__(self, *, device_name: str | None, queue_frames: int) -> None:
        self._device_name = device_name
        self._queue: asyncio.Queue[AudioChunk] = asyncio.Queue(maxsize=queue_frames)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stream: object | None = None
        self._accepting = False
        self._generation = 0
        self._dropped_frames = 0
        self._overflow_frames = 0
        self._callback_errors = 0
        self._last_drop_log_at = 0.0
        self._last_callback_at: float | None = None
        self._capture_sample_rate = INPUT_SAMPLE_RATE
        self._reset_signal_state()

    async def start(self) -> None:
        if self._stream is not None:
            return
        self._loop = asyncio.get_running_loop()
        stream: object | None = None
        try:
            sounddevice = importlib.import_module("sounddevice")
            await asyncio.to_thread(_refresh_portaudio_devices, sounddevice)
            device = await asyncio.to_thread(
                _resolve_device_index,
                sounddevice,
                name=self._device_name,
                input_device=True,
            )
            stream = await self._open_input_stream(sounddevice, device)
            await asyncio.to_thread(stream.start)
        except asyncio.CancelledError:
            raise
        except VoiceFatalError:
            raise
        except Exception as error:
            if stream is not None:
                try:
                    await asyncio.to_thread(stream.close)
                except Exception:
                    pass
            raise VoiceFatalError("microphone_open_failed") from error
        self._stream = stream
        self._last_callback_at = time.monotonic()
        self.discard_pending()
        self._reset_signal_state()
        self._accepting = True

    async def _open_input_stream(self, sounddevice: object, device: int | None) -> object:
        """Prefer the pipeline rate and fall back to 48 kHz USB capture.

        Several USB microphones, including the deployed AKG device, reject a
        24 kHz ALSA stream even though they work at 48 kHz.  The callback
        downsamples the latter by two before handing data to the fixed 24 kHz
        wake-word/voice pipeline.
        """

        last_error: Exception | None = None
        for sample_rate in (INPUT_SAMPLE_RATE, FALLBACK_INPUT_SAMPLE_RATE):
            try:
                stream = await asyncio.to_thread(
                    sounddevice.RawInputStream,
                    samplerate=sample_rate,
                    blocksize=INPUT_FRAME_SAMPLES * sample_rate // INPUT_SAMPLE_RATE,
                    device=device,
                    channels=1,
                    dtype="int16",
                    callback=self._callback,
                )
            except Exception as error:
                last_error = error
                continue
            self._capture_sample_rate = sample_rate
            return stream
        assert last_error is not None
        raise last_error

    async def stop(self) -> None:
        self._accepting = False
        self.discard_pending()
        self._last_callback_at = None
        self._reset_signal_state()
        stream, self._stream = self._stream, None
        if stream is None:
            return
        errors: list[BaseException] = []
        for method_name in ("stop", "close"):
            try:
                await asyncio.to_thread(getattr(stream, method_name))
            except Exception as error:  # cleanup은 다음 단계까지 계속한다.
                errors.append(error)
        if errors:
            raise VoiceFatalError("microphone_close_failed") from errors[0]

    async def read(self, timeout_seconds: float | None = None) -> AudioChunk:
        try:
            if timeout_seconds is None:
                return await self._queue.get()
            async with asyncio.timeout(timeout_seconds):
                return await self._queue.get()
        except TimeoutError:
            stream = self._stream
            if stream is not None and not bool(getattr(stream, "active", True)):
                raise VoiceFatalError("microphone_inactive")
            last_callback_at = self._last_callback_at
            if (
                stream is not None
                and last_callback_at is not None
                and time.monotonic() - last_callback_at
                >= INPUT_CALLBACK_STALE_SECONDS
            ):
                raise VoiceFatalError("microphone_inactive")
            raise

    def set_accepting(self, enabled: bool) -> None:
        self._accepting = enabled

    def discard_pending(self) -> None:
        self._generation += 1
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _callback(
        self,
        indata: object,
        _frames: int,
        _time_info: object,
        status: object,
    ) -> None:
        try:
            pcm = bytes(indata)
            if self._capture_sample_rate == FALLBACK_INPUT_SAMPLE_RATE:
                pcm = np.frombuffer(pcm, dtype="<i2")[::2].tobytes()
            captured_at = time.monotonic()
            self._last_callback_at = captured_at
            input_overflow = bool(getattr(status, "input_overflow", False))
            has_status = bool(status)
            loop = self._loop
            if loop is not None:
                loop.call_soon_threadsafe(
                    self._enqueue_from_loop,
                    self._generation,
                    pcm,
                    captured_at,
                    input_overflow,
                    has_status,
                )
        except Exception:
            self._callback_errors += 1

    def _enqueue_from_loop(
        self,
        generation: int,
        pcm: bytes,
        captured_at: float,
        input_overflow: bool,
        has_status: bool,
    ) -> None:
        if generation != self._generation or not self._accepting:
            return
        if len(pcm) != INPUT_FRAME_BYTES:
            self._callback_errors += 1
            return
        self._record_signal_frame(analyze_signal_frame(pcm), captured_at)
        if input_overflow:
            self._overflow_frames += 1
            self._log_rate_limited("input_overflow")
        elif has_status:
            self._log_rate_limited("input_status")
        if self._queue.full():
            self._queue.get_nowait()
            self._dropped_frames += 1
            self._log_rate_limited("input_frame_dropped")
        self._queue.put_nowait(AudioChunk(pcm=pcm, captured_at=captured_at))

    def _reset_signal_state(self) -> None:
        self._noise_rms_history: deque[tuple[float, float]] = deque(
            maxlen=SIGNAL_NOISE_WINDOW_FRAMES
        )
        self._recent_peak_history: deque[tuple[float, float]] = deque(
            maxlen=SIGNAL_RECENT_PEAK_WINDOW_FRAMES
        )
        self._latest_signal: AudioSignalFrame | None = None
        self._recent_peak_dbfs: float | None = None
        self._estimated_noise_floor_dbfs: float | None = None
        self._estimated_snr_db: float | None = None
        self._clipped_frames = 0
        self._signal_frames = 0

    def _record_signal_frame(self, signal: AudioSignalFrame, captured_at: float) -> None:
        """event loop에서만 rolling input signal 통계를 갱신한다."""

        self._latest_signal = signal
        self._signal_frames += 1
        if signal.clipping_ratio > 0:
            self._clipped_frames += 1
        self._noise_rms_history.append((captured_at, signal.rms_dbfs))
        self._recent_peak_history.append((captured_at, signal.peak_dbfs))
        self._discard_expired_signal_history(captured_at)
        noise_values = [value for _, value in self._noise_rms_history]
        self._estimated_noise_floor_dbfs = float(np.percentile(noise_values, 20))
        self._estimated_snr_db = signal.rms_dbfs - self._estimated_noise_floor_dbfs
        self._recent_peak_dbfs = max(value for _, value in self._recent_peak_history)

    def _discard_expired_signal_history(self, captured_at: float) -> None:
        noise_before = captured_at - SIGNAL_NOISE_WINDOW_SECONDS
        while self._noise_rms_history and self._noise_rms_history[0][0] < noise_before:
            self._noise_rms_history.popleft()
        recent_peak_before = captured_at - SIGNAL_RECENT_PEAK_WINDOW_SECONDS
        while (
            self._recent_peak_history
            and self._recent_peak_history[0][0] < recent_peak_before
        ):
            self._recent_peak_history.popleft()

    def _log_rate_limited(self, event: str) -> None:
        now = time.monotonic()
        if self._last_drop_log_at and now - self._last_drop_log_at < 10:
            return
        self._last_drop_log_at = now
        LOGGER.warning(
            "음성 입력 상태를 확인해야 합니다.",
            extra={
                "component": "voice.audio",
                "event": event,
                "dropped_frames": self._dropped_frames,
                "queue_size": self._queue.qsize(),
            },
        )

    def get_debug_snapshot(self) -> AudioInputDebugSnapshot:
        """callback과 event loop 사이 queue 상태를 반환한다."""

        return AudioInputDebugSnapshot(
            accepting=self._accepting,
            queue_size=self._queue.qsize(),
            queue_capacity=self._queue.maxsize,
            dropped_frames=self._dropped_frames,
            overflow_frames=self._overflow_frames,
            callback_errors=self._callback_errors,
            latest_rms_dbfs=(
                None if self._latest_signal is None else self._latest_signal.rms_dbfs
            ),
            latest_peak_dbfs=(
                None if self._latest_signal is None else self._latest_signal.peak_dbfs
            ),
            recent_peak_dbfs=self._recent_peak_dbfs,
            estimated_noise_floor_dbfs=self._estimated_noise_floor_dbfs,
            estimated_snr_db=self._estimated_snr_db,
            latest_clipping_ratio=(
                None
                if self._latest_signal is None
                else self._latest_signal.clipping_ratio
            ),
            clipped_frames=self._clipped_frames,
            signal_frames=self._signal_frames,
            latest_dc_offset_pcm=(
                None if self._latest_signal is None else self._latest_signal.dc_offset_pcm
            ),
        )


class LocalPcmOutput:
    """24kHz mono PCM16을 48kHz stereo local speaker 출력으로 변환한다."""

    def __init__(self, *, device_name: str | None) -> None:
        self._device_name = device_name
        self._stream: object | None = None
        self._active = False

    async def start(self) -> None:
        if self._stream is not None:
            return
        try:
            sounddevice = importlib.import_module("sounddevice")
            device = await asyncio.to_thread(
                _resolve_device_index,
                sounddevice,
                name=self._device_name,
                input_device=False,
            )
            self._stream = await asyncio.to_thread(
                sounddevice.RawOutputStream,
                samplerate=OUTPUT_DEVICE_SAMPLE_RATE,
                device=device,
                channels=2,
                dtype="int16",
            )
        except asyncio.CancelledError:
            raise
        except VoiceFatalError:
            raise
        except Exception as error:
            raise VoiceFatalError("speaker_open_failed") from error

    async def write(self, pcm: bytes) -> None:
        if not pcm or len(pcm) % 2:
            raise ValueError("speaker PCM은 비어 있지 않은 even-byte 데이터여야 합니다.")
        stream = self._stream
        if stream is None:
            raise VoiceFatalError("speaker_not_started")
        try:
            if not self._active:
                await asyncio.to_thread(stream.start)
                self._active = True
            await asyncio.to_thread(stream.write, _to_device_pcm(pcm))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise VoiceFatalError("speaker_failed") from error

    async def drain(self) -> None:
        stream = self._stream
        if stream is None or not self._active:
            return
        try:
            await asyncio.to_thread(stream.stop)
            self._active = False
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise VoiceFatalError("speaker_failed") from error

    async def abort(self) -> None:
        stream = self._stream
        if stream is None or not self._active:
            return
        try:
            await asyncio.to_thread(stream.abort)
        except Exception as error:
            raise VoiceFatalError("speaker_failed") from error
        finally:
            self._active = False

    async def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        errors: list[BaseException] = []
        try:
            if self._active:
                await asyncio.to_thread(stream.abort)
        except Exception as error:
            errors.append(error)
        self._active = False
        try:
            await asyncio.to_thread(stream.close)
        except Exception as error:
            errors.append(error)
        if errors:
            raise VoiceFatalError("speaker_close_failed") from errors[0]


def _to_device_pcm(pcm: bytes) -> bytes:
    """24kHz mono PCM16을 48kHz stereo PCM16으로 변환한다."""

    samples = np.frombuffer(pcm, dtype="<i2")
    upsampled = np.repeat(samples, 2)
    return np.repeat(upsampled[:, np.newaxis], 2, axis=1).tobytes()
