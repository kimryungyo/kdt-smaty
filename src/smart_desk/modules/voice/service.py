"""Wake Word부터 follow-up까지 local Voice 상태 머신을 구현한다."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
import logging
from math import ceil
import time

from smart_desk.config.settings import VoiceSettings
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.assistant.openai import OpenAiGatewayPort, OpenAiTurnError
from smart_desk.modules.assistant.service import AssistantService, normalize_text
from smart_desk.modules.voice.audio import AudioInput, RmsRecorder, calculate_rms
from smart_desk.modules.voice.models import (
    AudioChunk,
    EffectName,
    INPUT_FRAME_SECONDS,
    RecordingEnd,
    RecordingTrigger,
    VoiceFatalError,
    VoiceSnapshot,
    VoiceState,
)
from smart_desk.modules.voice.playback import PlaybackCoordinator
from smart_desk.modules.voice.wakeword import WakeWordDetector


LOGGER = logging.getLogger(__name__)


class VoiceService:
    """microphone부터 speaker까지 하나의 순차 voice turn을 소유한다."""

    def __init__(
        self,
        *,
        audio_input: AudioInput,
        wakeword: WakeWordDetector,
        recorder: RmsRecorder,
        gateway: OpenAiGatewayPort,
        assistant: AssistantService,
        playback: PlaybackCoordinator,
        settings: VoiceSettings,
        task_manager: TaskManager,
    ) -> None:
        self._audio = audio_input
        self._wakeword = wakeword
        self._recorder = recorder
        self._gateway = gateway
        self._assistant = assistant
        self._playback = playback
        self._settings = settings
        self._task_manager = task_manager
        self._state = VoiceState.DISABLED
        self._snapshot = VoiceSnapshot(
            state=VoiceState.DISABLED,
            last_transition_at=datetime.now(timezone.utc),
            followup_expires_at=None,
            last_error=None,
        )
        self._followup_deadline: float | None = None
        self._followup_expires_at: datetime | None = None
        self._main_task: asyncio.Task[object] | None = None
        self._stopping = False
        self._detector_started = False
        self._playback_started = False
        self._input_started = False
        self._gateway_closed = False

    async def start(self) -> None:
        if self._main_task is not None and not self._main_task.done():
            return
        if self._state is VoiceState.ERROR:
            return
        self._stopping = False
        try:
            await self._wakeword.start()
            self._detector_started = True
            await self._playback.start()
            self._playback_started = True
            await self._audio.start()
            self._input_started = True
            self._audio.discard_pending()
            self._wakeword.reset()
            self._enter_waiting_wake(clear_followup=True)
            self._main_task = self._task_manager.create(
                "voice-main",
                self._run(),
                critical=False,
            )
        except asyncio.CancelledError:
            await self._cleanup_started_resources(
                close_gateway=True,
                partial_start=True,
            )
            raise
        except VoiceFatalError as error:
            await self._cleanup_started_resources(
                close_gateway=True,
                partial_start=True,
            )
            self._transition(VoiceState.ERROR, last_error=error.code)
        except Exception:
            await self._cleanup_started_resources(
                close_gateway=True,
                partial_start=True,
            )
            self._transition(VoiceState.ERROR, last_error="voice_start_failed")

    async def stop(self) -> None:
        if self._stopping and self._state is VoiceState.DISABLED:
            return
        self._stopping = True
        self._audio.set_accepting(False)
        self._audio.discard_pending()
        task, self._main_task = self._main_task, None
        if task is not None and not task.done():
            task.cancel()
        await self._cleanup_call(self._playback.stop_speech, "speech_abort_failed")
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await self._cleanup_started_resources(close_gateway=True)
        self._followup_deadline = None
        self._followup_expires_at = None
        self._transition(VoiceState.DISABLED, clear_error=True)

    def get_snapshot(self) -> VoiceSnapshot:
        return self._snapshot

    async def _run(self) -> None:
        try:
            while not self._stopping:
                if self._state is VoiceState.WAITING_WAKE:
                    try:
                        chunk = await self._audio.read(timeout_seconds=1.0)
                    except TimeoutError:
                        continue
                    if await self._wakeword.detect(chunk.pcm):
                        await self._run_turn(RecordingTrigger.WAKE_WORD)
                elif self._state is VoiceState.WAITING_FOLLOWUP:
                    candidate = await self._wait_for_followup_candidate()
                    if candidate is None:
                        self._enter_waiting_wake(clear_followup=True)
                    else:
                        pre_roll, streak = candidate
                        await self._run_turn(
                            RecordingTrigger.FOLLOWUP,
                            initial_chunks=pre_roll,
                            initial_above_threshold_frames=streak,
                            original_followup_deadline=self._followup_deadline,
                        )
                else:
                    raise VoiceFatalError("voice_state_invalid")
        except asyncio.CancelledError:
            raise
        except VoiceFatalError as error:
            await self._enter_fatal_error(error.code)
        except Exception:
            await self._enter_fatal_error("voice_main_failed")
            raise

    async def _run_turn(
        self,
        trigger: RecordingTrigger,
        *,
        initial_chunks: tuple[AudioChunk, ...] = (),
        initial_above_threshold_frames: int = 0,
        original_followup_deadline: float | None = None,
    ) -> None:
        self._transition(VoiceState.RECORDING)
        if trigger is RecordingTrigger.WAKE_WORD:
            self._audio.set_accepting(False)
            self._audio.discard_pending()
            await self._playback.play_effect(EffectName.ACKNOWLEDGEMENT)
            self._audio.discard_pending()
            self._audio.set_accepting(True)

        started = time.monotonic()
        speech_start_deadline = (
            original_followup_deadline
            if trigger is RecordingTrigger.FOLLOWUP
            else started + self._settings.speech_start_timeout_seconds
        )
        if speech_start_deadline is None:
            raise VoiceFatalError("followup_deadline_missing")
        utterance, end = await self._recorder.record(
            self._audio,
            speech_start_deadline=speech_start_deadline,
            initial_chunks=initial_chunks,
            initial_above_threshold_frames=initial_above_threshold_frames,
        )
        if utterance is None:
            self._return_after_empty_recording(
                trigger,
                end,
                original_followup_deadline,
            )
            return

        self._audio.set_accepting(False)
        self._audio.discard_pending()
        self._transition(VoiceState.PROCESSING)
        try:
            transcript = normalize_text(await self._gateway.transcribe(utterance))
        except OpenAiTurnError as error:
            await self._recover_turn_error(error.code)
            return
        if not transcript:
            await self._recover_empty_transcript(original_followup_deadline)
            return
        try:
            reply = await self._assistant.reply(transcript)
        except OpenAiTurnError as error:
            await self._recover_turn_error(error.code)
            return

        self._transition(VoiceState.SPEAKING)
        try:
            await self._playback.play_speech(
                self._gateway.synthesize(reply.spoken_text)
            )
        except OpenAiTurnError as error:
            await self._recover_turn_error(error.code)
            return
        await self._after_successful_playback()

    async def _wait_for_followup_candidate(
        self,
    ) -> tuple[tuple[AudioChunk, ...], int] | None:
        deadline = self._followup_deadline
        if deadline is None:
            raise VoiceFatalError("followup_deadline_missing")
        preroll: deque[AudioChunk] = deque(
            maxlen=ceil(
                self._settings.followup_preroll_seconds / INPUT_FRAME_SECONDS
            )
        )
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                chunk = await self._audio.read(timeout_seconds=remaining)
            except TimeoutError:
                return None
            preroll.append(chunk)
            if calculate_rms(chunk.pcm) >= self._settings.silence_rms_threshold:
                return tuple(preroll), 1

    def _return_after_empty_recording(
        self,
        trigger: RecordingTrigger,
        end: RecordingEnd,
        original_followup_deadline: float | None,
    ) -> None:
        del end
        if (
            trigger is RecordingTrigger.FOLLOWUP
            and original_followup_deadline is not None
            and time.monotonic() < original_followup_deadline
        ):
            self._transition(VoiceState.WAITING_FOLLOWUP)
            return
        self._enter_waiting_wake(clear_followup=True)

    async def _recover_empty_transcript(
        self,
        original_followup_deadline: float | None,
    ) -> None:
        await self._play_error_effect()
        self._audio.discard_pending()
        self._audio.set_accepting(True)
        if (
            original_followup_deadline is not None
            and time.monotonic() < original_followup_deadline
        ):
            self._transition(VoiceState.WAITING_FOLLOWUP)
        else:
            self._enter_waiting_wake(clear_followup=True)

    async def _recover_turn_error(self, code: str) -> None:
        await self._play_error_effect()
        self._enter_waiting_wake(clear_followup=True, last_error=code)

    async def _play_error_effect(self) -> None:
        self._audio.set_accepting(False)
        self._audio.discard_pending()
        await self._playback.play_effect(EffectName.ERROR)
        self._audio.discard_pending()

    async def _after_successful_playback(self) -> None:
        self._audio.set_accepting(False)
        self._audio.discard_pending()
        await asyncio.sleep(self._settings.post_playback_guard_seconds)
        self._audio.discard_pending()
        self._audio.set_accepting(True)
        if self._settings.followup_enabled:
            self._open_followup_window()
        else:
            self._enter_waiting_wake(clear_followup=True, clear_error=True)

    def _open_followup_window(self) -> None:
        timeout = self._settings.followup_timeout_seconds
        self._followup_deadline = time.monotonic() + timeout
        expires = datetime.now(timezone.utc) + timedelta(seconds=timeout)
        self._followup_expires_at = expires
        self._transition(
            VoiceState.WAITING_FOLLOWUP,
            followup_expires_at=expires,
            clear_error=True,
        )

    def _enter_waiting_wake(
        self,
        *,
        clear_followup: bool,
        last_error: str | None = None,
        clear_error: bool = False,
    ) -> None:
        if clear_followup:
            self._followup_deadline = None
            self._followup_expires_at = None
        self._audio.discard_pending()
        self._audio.set_accepting(True)
        self._wakeword.reset()
        self._transition(
            VoiceState.WAITING_WAKE,
            last_error=last_error,
            clear_error=clear_error,
        )

    async def _enter_fatal_error(self, code: str) -> None:
        self._audio.set_accepting(False)
        self._audio.discard_pending()
        await self._cleanup_call(self._playback.stop_speech, "speech_abort_failed")
        self._transition(VoiceState.ERROR, last_error=code)

    def _transition(
        self,
        state: VoiceState,
        *,
        followup_expires_at: datetime | None = None,
        last_error: str | None = None,
        clear_error: bool = False,
    ) -> None:
        previous = self._state
        self._state = state
        retained_error = None if clear_error else self._snapshot.last_error
        self._snapshot = VoiceSnapshot(
            state=state,
            last_transition_at=datetime.now(timezone.utc),
            followup_expires_at=(
                followup_expires_at or self._followup_expires_at
                if state is VoiceState.WAITING_FOLLOWUP
                else None
            ),
            last_error=last_error if last_error is not None else retained_error,
        )
        LOGGER.info(
            "Voice 상태가 전환되었습니다.",
            extra={
                "component": "voice",
                "event": "state_transition",
                "from_state": previous.value,
                "to_state": state.value,
            },
        )

    async def _cleanup_started_resources(
        self,
        *,
        close_gateway: bool,
        partial_start: bool = False,
    ) -> None:
        if self._input_started:
            await self._cleanup_call(self._audio.stop, "microphone_close_failed")
            self._input_started = False
        if partial_start:
            if self._playback_started:
                await self._cleanup_call(self._playback.stop, "speaker_close_failed")
                self._playback_started = False
            if self._detector_started:
                await self._cleanup_call(self._wakeword.stop, "wakeword_close_failed")
                self._detector_started = False
        else:
            if self._detector_started:
                await self._cleanup_call(self._wakeword.stop, "wakeword_close_failed")
                self._detector_started = False
            if self._playback_started:
                await self._cleanup_call(self._playback.stop, "speaker_close_failed")
                self._playback_started = False
        if close_gateway and not self._gateway_closed:
            await self._cleanup_call(self._gateway.close, "openai_close_failed")
            self._gateway_closed = True

    async def _cleanup_call(self, method: object, code: str) -> None:
        try:
            await method()  # type: ignore[operator]
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.warning(
                "Voice 자원 정리 중 오류가 발생했습니다.",
                extra={
                    "component": "voice",
                    "event": "cleanup_failed",
                    "error_code": code,
                },
            )
