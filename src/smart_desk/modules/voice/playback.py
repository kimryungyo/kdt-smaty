"""효과음과 streaming TTS의 local speaker 출력을 직렬화한다."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import logging
from pathlib import Path
import wave

from smart_desk.modules.voice.audio import PcmOutput
from smart_desk.modules.voice.models import EffectName, OUTPUT_SAMPLE_RATE, VoiceFatalError


LOGGER = logging.getLogger(__name__)


def _read_effect(path: Path, *, max_duration_seconds: float) -> bytes:
    try:
        with wave.open(str(path), "rb") as wav_file:
            if (
                wav_file.getnchannels() != 1
                or wav_file.getsampwidth() != 2
                or wav_file.getframerate() != OUTPUT_SAMPLE_RATE
                or wav_file.getcomptype() != "NONE"
            ):
                raise ValueError("effect WAV format이 24kHz mono PCM16이 아닙니다.")
            if wav_file.getnframes() / OUTPUT_SAMPLE_RATE > max_duration_seconds:
                raise ValueError("effect WAV가 허용 길이를 넘었습니다.")
            pcm = wav_file.readframes(wav_file.getnframes())
    except Exception as error:
        raise VoiceFatalError("voice_effect_invalid") from error
    if not pcm or len(pcm) % 2:
        raise VoiceFatalError("voice_effect_invalid")
    return pcm


class PlaybackCoordinator:
    """하나의 output stream에서 effect와 TTS가 겹치지 않게 한다."""

    def __init__(
        self,
        output: PcmOutput,
        *,
        acknowledgement_effect_path: Path,
        error_effect_path: Path,
    ) -> None:
        self._output = output
        self._effect_paths = {
            EffectName.ACKNOWLEDGEMENT: acknowledgement_effect_path,
            EffectName.ERROR: error_effect_path,
        }
        self._effects: dict[EffectName, bytes] = {}
        self._output_lock = asyncio.Lock()
        self._speech_task: asyncio.Task[object] | None = None
        self._started = False
        self._stopping = False

    async def start(self) -> None:
        if self._started:
            return
        effects = await asyncio.to_thread(
            lambda: {
                EffectName.ACKNOWLEDGEMENT: _read_effect(
                    self._effect_paths[EffectName.ACKNOWLEDGEMENT],
                    max_duration_seconds=0.12,
                ),
                EffectName.ERROR: _read_effect(
                    self._effect_paths[EffectName.ERROR],
                    max_duration_seconds=0.3,
                ),
            }
        )
        try:
            await self._output.start()
        except BaseException:
            try:
                await self._output.stop()
            except Exception:
                pass
            raise
        self._effects = effects
        self._started = True
        self._stopping = False

    async def stop(self) -> None:
        if self._stopping and not self._started:
            return
        self._stopping = True
        await self.stop_speech()
        if self._started:
            await self._output.stop()
        self._effects.clear()
        self._started = False

    async def play_effect(self, effect: EffectName) -> None:
        self._require_playable()
        try:
            async with self._output_lock:
                await self._output.write(self._effects[effect])
                await self._output.drain()
        except asyncio.CancelledError:
            await self._output.abort()
            raise
        except Exception:
            await self._output.abort()
            raise

    async def play_speech(self, chunks: AsyncIterator[bytes]) -> None:
        self._require_playable()
        current = asyncio.current_task()
        if current is None:
            raise RuntimeError("TTS playback task를 확인할 수 없습니다.")
        self._speech_task = current
        remainder = b""
        chunk_count = 0
        byte_count = 0
        try:
            async with self._output_lock:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    combined = remainder + chunk
                    aligned_length = len(combined) - (len(combined) % 2)
                    if aligned_length:
                        await self._output.write(combined[:aligned_length])
                        chunk_count += 1
                        byte_count += aligned_length
                    remainder = combined[aligned_length:]
                if remainder:
                    raise ValueError("TTS PCM stream이 불완전한 sample로 끝났습니다.")
                if byte_count == 0:
                    raise VoiceFatalError("voice_response_audio_missing")
                LOGGER.info(
                    "로컬 스피커 drain을 시작합니다.",
                    extra={
                        "component": "voice.playback",
                        "event": "speaker_drain_started",
                        "tts_audio_chunks": chunk_count,
                        "tts_audio_bytes": byte_count,
                    },
                )
                await self._output.drain()
                LOGGER.info(
                    "로컬 스피커 drain이 완료되었습니다.",
                    extra={
                        "component": "voice.playback",
                        "event": "speaker_drain_finished",
                        "tts_audio_chunks": chunk_count,
                        "tts_audio_bytes": byte_count,
                    },
                )
        except asyncio.CancelledError:
            await self._output.abort()
            raise
        except Exception:
            await self._output.abort()
            raise
        finally:
            if self._speech_task is current:
                self._speech_task = None

    async def stop_speech(self) -> None:
        task = self._speech_task
        current = asyncio.current_task()
        if task is not None and task is not current and not task.done():
            task.cancel()
        try:
            await self._output.abort()
        finally:
            if task is not None and task is not current:
                await asyncio.gather(task, return_exceptions=True)

    def _require_playable(self) -> None:
        if not self._started or self._stopping:
            raise VoiceFatalError("speaker_not_started")
