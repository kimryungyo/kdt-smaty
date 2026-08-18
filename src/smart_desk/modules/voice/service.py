"""Local wake-word/audio coordination for the single Agents voice runtime."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterable, AsyncIterator
from datetime import datetime, timedelta, timezone
import logging
from math import ceil
import time
from typing import Protocol

from smart_desk.config.settings import VoiceSettings
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.assistant.agents_runtime import (
    VoiceRuntimeEventType,
    VoiceRuntimeLifecycle,
)
from smart_desk.modules.voice.audio import AudioInput, calculate_rms
from smart_desk.modules.voice.models import (
    AudioChunk,
    EffectName,
    INPUT_FRAME_SECONDS,
    VoiceFatalError,
    VoiceSnapshot,
    VoiceState,
)
from smart_desk.modules.voice.playback import PlaybackCoordinator
from smart_desk.modules.voice.wakeword import WakeWordDetector


LOGGER = logging.getLogger(__name__)
# The deployment normally recovers the microphone with one planned restart.
# A slow fallback retry avoids flooding logs while the configured device is unplugged.
DEVICE_RETRY_INTERVAL_SECONDS = 30.0
RECOVERABLE_DEVICE_ERRORS = frozenset({
    "input_device_name_invalid", "microphone_inactive", "microphone_open_failed",
    "output_device_name_invalid", "speaker_open_failed",
})


class AgentsVoiceRuntimePort(Protocol):
    def run_audio(self, chunks: AsyncIterable[bytes]) -> AsyncIterator[object]: ...
    async def stop(self) -> None: ...
    async def finalize_turn(self, outcome: str, *, error_code: str | None = None) -> None: ...


class VoiceService:
    """Own one short SDK voice execution per wake/follow-up turn."""

    def __init__(
        self, *, audio_input: AudioInput, wakeword: WakeWordDetector,
        runtime: AgentsVoiceRuntimePort, playback: PlaybackCoordinator,
        settings: VoiceSettings, task_manager: TaskManager,
    ) -> None:
        self._audio = audio_input
        self._wakeword = wakeword
        self._runtime = runtime
        self._playback = playback
        self._settings = settings
        self._task_manager = task_manager
        self._state = VoiceState.DISABLED
        self._snapshot = VoiceSnapshot(VoiceState.DISABLED, datetime.now(timezone.utc), None, None)
        self._main_task: asyncio.Task[object] | None = None
        self._turn_task: asyncio.Task[object] | None = None
        self._stopping = False
        self._detector_started = False
        self._input_started = False
        self._playback_started = False
        self._runtime_closed = False
        self._followup_deadline: float | None = None
        self._followup_expires_at: datetime | None = None
        self._input_stop: asyncio.Event | None = None

    async def start(self) -> None:
        if self._main_task is not None and not self._main_task.done():
            return
        self._stopping = False
        try:
            await self._start_components()
        except asyncio.CancelledError:
            await self._cleanup_started_resources(partial_start=True)
            raise
        except VoiceFatalError as error:
            await self._cleanup_started_resources(partial_start=True)
            if error.code not in RECOVERABLE_DEVICE_ERRORS:
                self._transition(VoiceState.ERROR, last_error=error.code)
                return
            self._transition_device_retry(error.code)
        except Exception:
            await self._cleanup_started_resources(partial_start=True)
            self._transition(VoiceState.ERROR, last_error="voice_start_failed")
            return
        self._main_task = self._task_manager.create("voice-main", self._supervise(), critical=False)

    async def stop(self) -> None:
        if self._stopping and self._state is VoiceState.DISABLED:
            return
        self._stopping = True
        self._audio.set_accepting(False)
        self._audio.discard_pending()
        for task in (self._turn_task, self._main_task):
            if task is not None and not task.done():
                task.cancel()
        await self._cleanup_call(self._playback.stop_speech, "speech_abort_failed")
        await asyncio.gather(
            *(task for task in (self._turn_task, self._main_task) if task is not None),
            return_exceptions=True,
        )
        self._turn_task = None
        self._main_task = None
        await self._cleanup_started_resources(partial_start=False)
        if not self._runtime_closed:
            await self._cleanup_call(self._runtime.stop, "openai_close_failed")
            self._runtime_closed = True
        self._followup_deadline = None
        self._followup_expires_at = None
        self._transition(VoiceState.DISABLED, clear_error=True)

    def get_snapshot(self) -> VoiceSnapshot:
        return self._snapshot

    async def _start_components(self) -> None:
        await self._wakeword.start()
        self._detector_started = True
        await self._audio.start()
        self._input_started = True
        await self._playback.start()
        self._playback_started = True
        self._audio.discard_pending()
        self._wakeword.reset()
        self._enter_waiting_wake(clear_followup=True, clear_error=True)

    async def _supervise(self) -> None:
        retry = self._state is VoiceState.ERROR
        while not self._stopping:
            try:
                if retry:
                    await asyncio.sleep(DEVICE_RETRY_INTERVAL_SECONDS)
                    await self._start_components()
                    retry = False
                await self._run()
                return
            except asyncio.CancelledError:
                raise
            except VoiceFatalError as error:
                await self._cleanup_started_resources(partial_start=True)
                if error.code not in RECOVERABLE_DEVICE_ERRORS:
                    self._transition(VoiceState.ERROR, last_error=error.code)
                    return
                self._transition_device_retry(error.code)
                retry = True
            except Exception:
                await self._cleanup_started_resources(partial_start=True)
                self._transition(VoiceState.ERROR, last_error="voice_start_failed")
                return

    async def _run(self) -> None:
        while not self._stopping:
            if self._state is VoiceState.WAITING_WAKE:
                try:
                    chunk = await self._audio.read(timeout_seconds=1.0)
                except TimeoutError:
                    continue
                if await self._wakeword.detect(chunk.pcm):
                    await self._run_turn((), speech_already_started=False)
            elif self._state is VoiceState.WAITING_FOLLOWUP:
                initial = await self._wait_for_followup_candidate()
                if initial is None:
                    self._enter_waiting_wake(clear_followup=True)
                else:
                    await self._run_turn(initial, speech_already_started=True)
            else:
                raise VoiceFatalError("voice_state_invalid")

    async def _run_turn(self, initial: tuple[AudioChunk, ...], *, speech_already_started: bool) -> None:
        self._transition(VoiceState.RECORDING)
        if not initial:
            self._audio.set_accepting(False)
            self._audio.discard_pending()
            await self._playback.play_effect(EffectName.ACKNOWLEDGEMENT)
            self._audio.discard_pending()
            self._audio.set_accepting(True)
        self._turn_task = asyncio.create_task(
            self._consume_runtime(initial, speech_already_started=speech_already_started)
        )
        try:
            await self._turn_task
        except asyncio.CancelledError:
            if self._stopping:
                raise
            await self._finalize("CANCELLED")
            await self._cleanup_call(self._playback.stop_speech, "speech_abort_failed")
            self._enter_waiting_wake(clear_followup=True)
        except Exception:
            await self._finalize("FAILED", error_code="voice_pipeline_failed")
            await self._recover_turn_error("voice_pipeline_failed")
        finally:
            self._turn_task = None

    async def _consume_runtime(self, initial: tuple[AudioChunk, ...], *, speech_already_started: bool) -> None:
        input_stopped = False
        saw_transcript = False
        saw_turn_ended = False
        followup_requested = False
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=4)
        self._input_stop = asyncio.Event()
        playback_task: asyncio.Task[object] | None = None
        audio_chunk_count = 0

        async def speaker() -> AsyncIterator[bytes]:
            while True:
                item = await queue.get()
                if item is None:
                    return
                yield item

        async def enqueue(item: bytes | None) -> None:
            """Never wait on a full queue after the speaker has failed."""
            put_task = asyncio.create_task(queue.put(item))
            wait_for = {put_task}
            if playback_task is not None:
                wait_for.add(playback_task)
            done, _ = await asyncio.wait(wait_for, return_when=asyncio.FIRST_COMPLETED)
            if playback_task is not None and playback_task in done:
                put_task.cancel()
                await asyncio.gather(put_task, return_exceptions=True)
                await playback_task
            await put_task

        try:
            LOGGER.info(
                "Agents 음성 runtime event stream을 수신합니다.",
                extra={"component": "voice", "event": "runtime_stream_started"},
            )
            async for event in self._runtime.run_audio(
                self._audio_chunks(initial, speech_already_started=speech_already_started)
            ):
                event_type = getattr(event, "type", None)
                if event_type is VoiceRuntimeEventType.TRANSCRIPT:
                    saw_transcript = True
                    self._transition(VoiceState.PROCESSING)
                    self._input_stop.set()
                    self._audio.set_accepting(False)
                    self._audio.discard_pending()
                    input_stopped = True
                elif event_type is VoiceRuntimeEventType.AUDIO:
                    if not saw_transcript:
                        raise VoiceFatalError("voice_pipeline_failed")
                    if not input_stopped:
                        self._input_stop.set()
                        self._audio.set_accepting(False)
                        self._audio.discard_pending()
                        input_stopped = True
                    if playback_task is None:
                        self._transition(VoiceState.SPEAKING)
                        playback_task = asyncio.create_task(self._playback.play_speech(speaker()))
                    audio = getattr(event, "audio", None)
                    if audio:
                        audio_chunk_count += 1
                        if audio_chunk_count == 1:
                            LOGGER.info(
                                "첫 TTS 오디오 청크를 받아 로컬 재생을 시작합니다.",
                                extra={
                                    "component": "voice",
                                    "event": "tts_first_audio_received",
                                    "audio_bytes": len(audio),
                                },
                            )
                        await enqueue(audio)
                elif event_type is VoiceRuntimeEventType.ERROR:
                    raise VoiceFatalError("voice_pipeline_failed")
                elif (
                    event_type is VoiceRuntimeEventType.LIFECYCLE
                    and getattr(event, "lifecycle", None) is VoiceRuntimeLifecycle.TURN_ENDED
                ):
                    saw_turn_ended = True
                    followup_requested = bool(getattr(event, "followup_requested", False))
                    LOGGER.info(
                        "Agents 음성 runtime이 turn 종료를 보고했습니다.",
                        extra={
                            "component": "voice",
                            "event": "runtime_turn_ended",
                            "tts_audio_chunks": audio_chunk_count,
                            "followup_requested": followup_requested,
                        },
                    )
                elif (
                    event_type is VoiceRuntimeEventType.LIFECYCLE
                    and getattr(event, "lifecycle", None) is VoiceRuntimeLifecycle.SESSION_ENDED
                ):
                    raise asyncio.CancelledError

            # No final transcript is an empty/no-speech turn, not provider failure.
            if not saw_transcript:
                await self._finalize("CANCELLED")
                self._enter_waiting_wake(clear_followup=True, clear_error=True)
                return
            if playback_task is not None:
                LOGGER.info(
                    "TTS stream 종료 후 스피커 drain을 기다립니다.",
                    extra={
                        "component": "voice",
                        "event": "speaker_drain_wait_started",
                        "tts_audio_chunks": audio_chunk_count,
                    },
                )
                await enqueue(None)
                await playback_task
                LOGGER.info(
                    "스피커 drain이 완료되었습니다.",
                    extra={
                        "component": "voice",
                        "event": "speaker_drain_completed",
                        "tts_audio_chunks": audio_chunk_count,
                    },
                )
            if not saw_turn_ended:
                raise VoiceFatalError("voice_pipeline_failed")
            await self._finalize("SUCCEEDED")
            # This guard remains inside the registered public turn task.  There is no
            # parent await between speaker drain and opening the follow-up window.
            self._audio.set_accepting(False)
            self._audio.discard_pending()
            await asyncio.sleep(self._settings.post_playback_guard_seconds)
            if followup_requested and self._settings.followup_enabled and not self._stopping:
                self._audio.set_accepting(True)
                self._open_followup_window()
            else:
                self._enter_waiting_wake(clear_followup=True, clear_error=True)
        finally:
            self._input_stop.set()
            if playback_task is not None and not playback_task.done():
                playback_task.cancel()
            if playback_task is not None:
                await asyncio.gather(playback_task, return_exceptions=True)
            self._input_stop = None

    async def _audio_chunks(self, initial: tuple[AudioChunk, ...], *, speech_already_started: bool) -> AsyncIterator[bytes]:
        """Yield original 24kHz PCM; RMS only ends the wait *for speech to start*."""
        speech_started = speech_already_started
        deadline = None if speech_started else time.monotonic() + self._settings.speech_start_timeout_seconds
        for chunk in initial:
            yield chunk.pcm
        while not self._stopping and not (self._input_stop and self._input_stop.is_set()):
            timeout = None if speech_started else max(0.0, (deadline or 0.0) - time.monotonic())
            if timeout is not None and timeout <= 0:
                return
            try:
                chunk = await self._audio.read(timeout_seconds=timeout)
            except TimeoutError:
                return
            yield chunk.pcm
            if not speech_started and calculate_rms(chunk.pcm) >= self._settings.silence_rms_threshold:
                speech_started = True

    async def _wait_for_followup_candidate(self) -> tuple[AudioChunk, ...] | None:
        deadline = self._followup_deadline
        if deadline is None:
            raise VoiceFatalError("followup_deadline_missing")
        pre_roll: deque[AudioChunk] = deque(maxlen=ceil(self._settings.followup_preroll_seconds / INPUT_FRAME_SECONDS))
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                chunk = await self._audio.read(timeout_seconds=remaining)
            except TimeoutError:
                return None
            pre_roll.append(chunk)
            if calculate_rms(chunk.pcm) >= self._settings.silence_rms_threshold:
                return tuple(pre_roll)
        return None

    async def _finalize(self, outcome: str, *, error_code: str | None = None) -> None:
        finalize = getattr(self._runtime, "finalize_turn", None)
        if callable(finalize):
            await finalize(outcome, error_code=error_code)

    async def _recover_turn_error(self, code: str) -> None:
        self._audio.set_accepting(False)
        self._audio.discard_pending()
        await self._cleanup_call(lambda: self._playback.play_effect(EffectName.ERROR), "error_effect_failed")
        self._enter_waiting_wake(clear_followup=True, last_error=code)

    def _open_followup_window(self) -> None:
        timeout = self._settings.followup_timeout_seconds
        self._followup_deadline = time.monotonic() + timeout
        self._followup_expires_at = datetime.now(timezone.utc) + timedelta(seconds=timeout)
        self._transition(VoiceState.WAITING_FOLLOWUP, followup_expires_at=self._followup_expires_at, clear_error=True)

    def _enter_waiting_wake(self, *, clear_followup: bool, last_error: str | None = None, clear_error: bool = False) -> None:
        if clear_followup:
            self._followup_deadline = None
            self._followup_expires_at = None
        self._audio.discard_pending()
        self._audio.set_accepting(True)
        self._wakeword.reset()
        self._transition(VoiceState.WAITING_WAKE, last_error=last_error, clear_error=clear_error)

    def _transition_device_retry(self, code: str) -> None:
        self._transition(VoiceState.ERROR, last_error=code)
        LOGGER.warning("음성 장치를 사용할 수 없어 자동 재시도합니다.", extra={"component": "voice", "event": "device_retry_scheduled", "error_code": code, "retry_seconds": DEVICE_RETRY_INTERVAL_SECONDS})

    def _transition(self, state: VoiceState, *, followup_expires_at: datetime | None = None, last_error: str | None = None, clear_error: bool = False) -> None:
        previous = self._state
        self._state = state
        self._snapshot = VoiceSnapshot(
            state, datetime.now(timezone.utc),
            (followup_expires_at or self._followup_expires_at) if state is VoiceState.WAITING_FOLLOWUP else None,
            last_error if last_error is not None else (None if clear_error else self._snapshot.last_error),
        )
        LOGGER.info("Voice 상태가 전환되었습니다.", extra={"component": "voice", "event": "state_transition", "from_state": previous.value, "to_state": state.value})

    async def _cleanup_started_resources(self, *, partial_start: bool) -> None:
        if self._input_started:
            await self._cleanup_call(self._audio.stop, "microphone_close_failed")
            self._input_started = False
        order = ((self._playback.stop, "speaker_close_failed", "_playback_started"), (self._wakeword.stop, "wakeword_close_failed", "_detector_started")) if partial_start else ((self._wakeword.stop, "wakeword_close_failed", "_detector_started"), (self._playback.stop, "speaker_close_failed", "_playback_started"))
        for method, code, attribute in order:
            if getattr(self, attribute):
                await self._cleanup_call(method, code)
                setattr(self, attribute, False)

    async def _cleanup_call(self, method: object, code: str) -> None:
        try:
            await method()  # type: ignore[operator]
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.warning("Voice 자원 정리 중 오류가 발생했습니다.", extra={"component": "voice", "event": "cleanup_failed", "error_code": code})
