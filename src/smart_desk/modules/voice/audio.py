"""로컬 PCM 입출력, RMS 발화 녹음과 memory WAV 조립을 구현한다."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
import importlib
import io
import logging
from math import ceil
import time
from typing import Protocol
import wave

import numpy as np

from smart_desk.modules.voice.models import (
    AudioChunk,
    AudioUtterance,
    INPUT_FRAME_BYTES,
    INPUT_FRAME_SECONDS,
    INPUT_SAMPLE_RATE,
    OUTPUT_SAMPLE_RATE,
    RecordingEnd,
    RecordingResult,
    VoiceFatalError,
)


LOGGER = logging.getLogger(__name__)
OUTPUT_DEVICE_SAMPLE_RATE = 48_000


@dataclass(frozen=True, slots=True)
class AudioInputDebugSnapshot:
    """원본 PCM을 제외한 microphone queue 관측값이다."""

    accepting: bool
    queue_size: int
    queue_capacity: int
    dropped_frames: int
    overflow_frames: int
    callback_errors: int


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


def build_wav(pcm_frames: Sequence[bytes]) -> AudioUtterance:
    """16kHz mono PCM16 frame을 하나의 memory WAV로 조립한다."""

    if not pcm_frames:
        raise ValueError("WAV를 만들 PCM frame이 없습니다.")
    if any(len(frame) != INPUT_FRAME_BYTES for frame in pcm_frames):
        raise ValueError("WAV 입력에는 고정 크기 PCM frame만 사용할 수 있습니다.")
    pcm = b"".join(pcm_frames)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(INPUT_SAMPLE_RATE)
        wav_file.writeframes(pcm)
    return AudioUtterance(
        wav=buffer.getvalue(),
        duration_seconds=len(pcm) / (INPUT_SAMPLE_RATE * 2),
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

    async def start(self) -> None:
        if self._stream is not None:
            return
        self._loop = asyncio.get_running_loop()
        stream: object | None = None
        try:
            sounddevice = importlib.import_module("sounddevice")
            device = await asyncio.to_thread(
                _resolve_device_index,
                sounddevice,
                name=self._device_name,
                input_device=True,
            )
            stream = await asyncio.to_thread(
                sounddevice.RawInputStream,
                samplerate=INPUT_SAMPLE_RATE,
                blocksize=1_280,
                device=device,
                channels=1,
                dtype="int16",
                callback=self._callback,
            )
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
        self.discard_pending()
        self._accepting = True

    async def stop(self) -> None:
        self._accepting = False
        self.discard_pending()
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
            captured_at = time.monotonic()
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


class RmsRecorder:
    """RMS threshold와 bounded deadline으로 하나의 발화를 memory WAV로 만든다."""

    def __init__(
        self,
        *,
        rms_threshold: float,
        speech_start_consecutive_frames: int,
        silence_duration_seconds: float,
        min_utterance_seconds: float,
        max_utterance_seconds: float,
        preroll_seconds: float,
    ) -> None:
        self._rms_threshold = rms_threshold
        self._start_frames = speech_start_consecutive_frames
        self._silence_seconds = silence_duration_seconds
        self._min_seconds = min_utterance_seconds
        self._max_frames = ceil(max_utterance_seconds / INPUT_FRAME_SECONDS)
        self._preroll_frames = ceil(preroll_seconds / INPUT_FRAME_SECONDS)

    async def record(
        self,
        audio_input: AudioInput,
        *,
        speech_start_deadline: float,
        initial_chunks: tuple[AudioChunk, ...] = (),
        initial_above_threshold_frames: int = 0,
    ) -> RecordingResult:
        preroll: deque[AudioChunk] = deque(initial_chunks, maxlen=self._preroll_frames)
        streak = initial_above_threshold_frames
        first_high_at = initial_chunks[-1].captured_at if streak and initial_chunks else None
        frames: list[bytes] | None = None
        speech_started_at: float | None = None
        last_high_at: float | None = None
        silence_started_at: float | None = None
        utterance_deadline: float | None = None

        while True:
            now = time.monotonic()
            if frames is None and now >= speech_start_deadline:
                return None, RecordingEnd.SPEECH_START_TIMEOUT
            timeout = (
                max(0.0, speech_start_deadline - now)
                if frames is None
                else max(0.0, (utterance_deadline or now) - now)
            )
            try:
                chunk = await audio_input.read(timeout_seconds=timeout)
            except TimeoutError:
                if frames is None:
                    return None, RecordingEnd.SPEECH_START_TIMEOUT
                return self._finish(
                    frames,
                    speech_started_at,
                    last_high_at,
                    RecordingEnd.MAX_DURATION,
                )

            above = calculate_rms(chunk.pcm) >= self._rms_threshold
            if frames is None:
                preroll.append(chunk)
                if above:
                    if streak == 0:
                        first_high_at = chunk.captured_at
                    streak += 1
                else:
                    streak = 0
                    first_high_at = None
                if streak < self._start_frames:
                    continue
                frames = [item.pcm for item in preroll]
                speech_started_at = first_high_at or chunk.captured_at
                last_high_at = chunk.captured_at
                utterance_deadline = time.monotonic() + (
                    self._max_frames - len(frames)
                ) * INPUT_FRAME_SECONDS
                if len(frames) >= self._max_frames:
                    return self._finish(frames, speech_started_at, last_high_at, RecordingEnd.MAX_DURATION)
                continue

            frames.append(chunk.pcm)
            if above:
                last_high_at = chunk.captured_at
                silence_started_at = None
            else:
                if silence_started_at is None:
                    silence_started_at = chunk.captured_at
                if (
                    chunk.captured_at
                    - silence_started_at
                    + INPUT_FRAME_SECONDS
                    + 1e-9
                    >= self._silence_seconds
                ):
                    return self._finish(frames, speech_started_at, last_high_at, RecordingEnd.SILENCE)
            if len(frames) >= self._max_frames:
                return self._finish(frames, speech_started_at, last_high_at, RecordingEnd.MAX_DURATION)

    def _finish(
        self,
        frames: list[bytes],
        speech_started_at: float | None,
        last_high_at: float | None,
        end: RecordingEnd,
    ) -> RecordingResult:
        if speech_started_at is None or last_high_at is None:
            return None, RecordingEnd.TOO_SHORT
        voiced_seconds = last_high_at - speech_started_at + INPUT_FRAME_SECONDS
        if voiced_seconds + 1e-9 < self._min_seconds:
            return None, RecordingEnd.TOO_SHORT
        return build_wav(frames[: self._max_frames]), end
