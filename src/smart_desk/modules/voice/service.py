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
AUDIO_READ_POLL_SECONDS = 0.5
# 장치를 다시 열면 회복되는 오류들. USB 재연결처럼 이미 열어 둔 stream이
# 도중에 죽는 경우도 여기에 든다. 열 때 실패한 것만 복구 대상으로 두면
# 재연결 뒤 첫 재생에서 서비스가 영구히 멈춘다.
RECOVERABLE_DEVICE_ERRORS = frozenset({
    "input_device_name_invalid", "microphone_inactive", "microphone_open_failed",
    "output_device_name_invalid", "speaker_open_failed",
    "speaker_failed", "speaker_not_started", "speaker_close_failed",
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
        # 먼저 말을 거는 중인지. 그동안 main loop는 마이크를 건드리지 않는다.
        self._announce_lock = asyncio.Lock()
        self._announcement_done = asyncio.Event()
        self._announcement_done.set()
        # 디버그/원격 강제 웨이크. 세워지면 main loop가 실제 감지와 똑같이 turn을 연다.
        self._manual_wake = asyncio.Event()
        # 대기 중 시작한 read/inference가 announce 이후 늦게 완료되어 가짜 turn을
        # 열지 못하게 하는 세대 번호다.
        self._wake_epoch = 0
        # announce()는 main supervisor 밖에서 실행된다. 그 자리에서 발견한
        # speaker 장애를 supervisor에 넘겨 장치를 다시 열게 한다.
        self._announced_device_error: VoiceFatalError | None = None

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

    def trigger_wake(self) -> bool:
        """디버그/원격용으로 웨이크워드 인식과 동일한 turn을 강제로 시작한다.

        실제 감지가 하던 것과 똑같은 경로(`_run_turn`)를 타도록 main loop에 신호만
        넘긴다. 스피커로 다시 소리를 내지 않으므로 자기 인식 위험이 없다. 말할
        자리가 아니면(대기 중이 아니거나 이미 turn이 도는 중이면) 조용히 무시하고
        False를 돌려준다.
        """

        if self._stopping:
            return False
        if (
            self._state is not VoiceState.WAITING_WAKE
            or self._turn_task is not None
            or self._manual_wake.is_set()
        ):
            return False
        self._manual_wake.set()
        LOGGER.info(
            "수동 웨이크 트리거를 접수했습니다.",
            extra={"component": "voice", "event": "manual_wake_requested"},
        )
        return True

    async def announce(self, chunks: AsyncIterator[bytes]) -> bool:
        """호출한 쪽이 만든 음성을 스피커로 먼저 들려준다.

        평소 turn은 깨우는 말에서 시작하지만, 사용자를 알아본 순간의 인사처럼
        기기가 먼저 말을 걸어야 할 때가 있다. 말할 자리가 아니면(대화 중이거나
        아직 준비가 안 됐으면) 조용히 지나가고 False를 돌려준다.
        """

        if self._stopping or not self._playback_started:
            return False
        if (
            self._state is not VoiceState.WAITING_WAKE
            or self._turn_task is not None
            or self._manual_wake.is_set()
        ):
            return False
        async with self._announce_lock:
            # 잠금을 기다리는 사이에 사용자가 말을 걸었을 수 있다.
            if (
                self._state is not VoiceState.WAITING_WAKE
                or self._turn_task is not None
                or self._manual_wake.is_set()
            ):
                return False
            self._announcement_done.clear()
            self._wake_epoch += 1
            # 스피커가 내는 소리를 마이크가 다시 듣고 깨어나지 않도록 입력을 닫는다.
            self._audio.set_accepting(False)
            self._audio.discard_pending()
            self._transition(VoiceState.SPEAKING)
            try:
                await self._playback.play_speech(chunks)
                return True
            except asyncio.CancelledError:
                raise
            except VoiceFatalError as error:
                if error.code in RECOVERABLE_DEVICE_ERRORS:
                    self._announced_device_error = error
                    self._transition(VoiceState.ERROR, last_error=error.code)
                LOGGER.warning(
                    "먼저 건네는 말을 재생하지 못했습니다.",
                    extra={
                        "component": "voice",
                        "event": "announcement_failed",
                        "error_code": error.code,
                    },
                )
                return False
            except Exception as error:
                LOGGER.warning(
                    "먼저 건네는 말을 재생하지 못했습니다.",
                    extra={
                        "component": "voice",
                        "event": "announcement_failed",
                        "error_code": getattr(error, "code", type(error).__name__),
                    },
                )
                return False
            finally:
                self._audio.discard_pending()
                self._wakeword.reset()
                self._audio.set_accepting(
                    not self._stopping and self._announced_device_error is None
                )
                if (
                    not self._stopping
                    and self._announced_device_error is None
                    and self._state is VoiceState.SPEAKING
                ):
                    self._enter_waiting_wake(clear_followup=True)
                self._announcement_done.set()

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
            if self._announced_device_error is not None:
                error, self._announced_device_error = (
                    self._announced_device_error,
                    None,
                )
                raise error
            if self._state is VoiceState.WAITING_WAKE:
                if await self._wait_for_wake():
                    await self._run_turn((), speech_already_started=False)
            elif self._state is VoiceState.WAITING_FOLLOWUP:
                initial = await self._wait_for_followup_candidate()
                if initial is None:
                    self._enter_waiting_wake(clear_followup=True)
                else:
                    await self._run_turn(initial, speech_already_started=True)
            elif self._state is VoiceState.SPEAKING:
                # announce()가 먼저 말을 거는 중이다. 끝날 때까지 마이크를 놔둔다.
                await self._announcement_done.wait()
            else:
                raise VoiceFatalError("voice_state_invalid")

    async def _wait_for_wake(self) -> bool:
        """마이크 감지와 수동 트리거를 함께 기다린다. 둘 중 무엇이든 turn을 연다."""
        wake_epoch = self._wake_epoch
        read_task = asyncio.ensure_future(self._audio.read(timeout_seconds=1.0))
        wake_task = asyncio.ensure_future(self._manual_wake.wait())
        try:
            await asyncio.wait({read_task, wake_task}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            for task in (read_task, wake_task):
                task.cancel()
            await asyncio.gather(read_task, wake_task, return_exceptions=True)
            raise
        # 수동 트리거를 우선한다. 같은 tick에 마이크 청크가 왔더라도 그 청크는 버린다.
        if self._manual_wake.is_set():
            self._manual_wake.clear()
            read_task.cancel()
            await asyncio.gather(read_task, return_exceptions=True)
            LOGGER.info(
                "수동 트리거로 turn을 시작합니다.",
                extra={"component": "voice", "event": "manual_wake_activated"},
            )
            return True
        wake_task.cancel()
        await asyncio.gather(wake_task, return_exceptions=True)
        try:
            chunk = read_task.result()
        except TimeoutError:
            return False
        if (
            wake_epoch != self._wake_epoch
            or self._state is not VoiceState.WAITING_WAKE
        ):
            return False
        detected = await self._wakeword.detect(chunk.pcm)
        return (
            detected
            and wake_epoch == self._wake_epoch
            and self._state is VoiceState.WAITING_WAKE
        )

    async def _run_turn(self, initial: tuple[AudioChunk, ...], *, speech_already_started: bool) -> None:
        if not initial:
            # ACK 재생 중에는 microphone frame을 폐기한다. 이 구간을 RECORDING으로
            # 공개하면 화면은 듣는 중이라고 표시하지만 실제 입력은 닫혀 있게 된다.
            self._transition(VoiceState.ACKNOWLEDGING)
            self._audio.set_accepting(False)
            self._audio.discard_pending()
            await self._playback.play_effect(EffectName.ACKNOWLEDGEMENT)
            self._audio.discard_pending()
            self._audio.set_accepting(True)
        self._transition(VoiceState.RECORDING)
        self._turn_task = asyncio.create_task(
            self._consume_runtime(initial, speech_already_started=speech_already_started)
        )
        try:
            async with asyncio.timeout(self._settings.turn_timeout_seconds):
                await self._turn_task
        except TimeoutError:
            LOGGER.warning(
                "음성 turn 전체 제한을 넘어 대기 상태로 복귀합니다.",
                extra={
                    "component": "voice",
                    "event": "turn_timeout",
                    "timeout_seconds": self._settings.turn_timeout_seconds,
                },
            )
            await self._finalize("FAILED", error_code="voice_turn_timeout")
            await self._cleanup_call(self._playback.stop_speech, "speech_abort_failed")
            await self._recover_turn_error("voice_turn_timeout")
        except asyncio.CancelledError:
            if self._stopping:
                raise
            await self._finalize("CANCELLED")
            await self._cleanup_call(self._playback.stop_speech, "speech_abort_failed")
            self._enter_waiting_wake(clear_followup=True)
        except VoiceFatalError as error:
            await self._finalize("FAILED", error_code=error.code)
            if error.code in RECOVERABLE_DEVICE_ERRORS:
                # 장치가 사라진 뒤에는 같은 stream으로 대기 상태에 돌아가 봐야
                # 다음 turn도 같은 자리에서 실패한다. supervisor가 장치를
                # 다시 열도록 올려보낸다.
                raise
            await self._recover_turn_error(error.code)
        except Exception:
            await self._finalize("FAILED", error_code="voice_pipeline_failed")
            await self._recover_turn_error("voice_pipeline_failed")
        finally:
            self._turn_task = None

    async def _consume_runtime(self, initial: tuple[AudioChunk, ...], *, speech_already_started: bool) -> None:
        input_stopped = False
        saw_transcript = False
        saw_processing = False
        saw_turn_ended = False
        followup_requested = False
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=4)
        self._input_stop = asyncio.Event()
        playback_task: asyncio.Task[object] | None = None
        audio_chunk_count = 0
        input_ended = asyncio.Event()
        speech_started = asyncio.Event()
        if speech_already_started:
            speech_started.set()
        input_ended_wait: asyncio.Task[bool] | None = None
        speech_started_wait: asyncio.Task[bool] | None = None
        event_wait: asyncio.Task[object] | None = None
        runtime_iterator: AsyncIterator[object] | None = None

        async def tracked_audio_chunks() -> AsyncIterator[bytes]:
            try:
                async for pcm in self._audio_chunks(
                    initial,
                    speech_already_started=speech_already_started,
                    speech_started_event=speech_started,
                ):
                    yield pcm
            finally:
                input_ended.set()

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
            runtime_stream = self._runtime.run_audio(tracked_audio_chunks())
            runtime_iterator = runtime_stream.__aiter__()
            input_ended_wait = asyncio.create_task(input_ended.wait())
            speech_started_wait = asyncio.create_task(speech_started.wait())
            recording_deadline = time.monotonic() + self._settings.recording_timeout_seconds
            while True:
                event_wait = asyncio.create_task(anext(runtime_iterator))
                while not event_wait.done():
                    wait_for_event: set[asyncio.Task[object]] = {event_wait}
                    if not saw_transcript and not speech_started.is_set():
                        wait_for_event.update((input_ended_wait, speech_started_wait))
                    if playback_task is not None:
                        # 출력 stream이 죽으면 다음 provider event나 전체 turn timeout까지
                        # SPEAKING에 고착시키지 않고 즉시 장치 복구 경로로 올린다.
                        wait_for_event.add(playback_task)
                    # 녹음 제한은 "사용자 발화가 STT로 확정되기를 기다리는" 한계다.
                    # 응답 재생이 시작되었다면 그 기다림은 이미 끝난 것이므로,
                    # 입력 전사가 늦게 오더라도 진행 중인 응답을 끊지 않는다.
                    remaining = (
                        max(0.0, recording_deadline - time.monotonic())
                        if not saw_transcript and not saw_processing and playback_task is None
                        else None
                    )
                    done, _ = await asyncio.wait(
                        wait_for_event,
                        timeout=remaining,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not done:
                        LOGGER.warning(
                            "STT 확정 event가 녹음 제한 안에 도착하지 않았습니다.",
                            extra={
                                "component": "voice",
                                "event": "recording_timeout",
                                "timeout_seconds": self._settings.recording_timeout_seconds,
                            },
                        )
                        raise VoiceFatalError("voice_recording_timeout")
                    if playback_task is not None and playback_task in done:
                        await playback_task
                        raise VoiceFatalError("voice_playback_ended_early")
                    # provider event가 input 종료와 같은 tick에 도착했다면 provider
                    # event를 먼저 처리한다. 특히 SPEECH_STARTED를 빈 발화로 오인해
                    # 정상 turn을 취소하지 않아야 한다.
                    if event_wait in done:
                        continue
                    if input_ended_wait in done and not speech_started.is_set():
                        # The SDK keeps its STT websocket open after StreamedAudioInput
                        # receives None. Close our one-turn consumer explicitly instead of
                        # inheriting the SDK's 1000-second event inactivity timeout.
                        event_wait.cancel()
                        await asyncio.gather(event_wait, return_exceptions=True)
                        LOGGER.info(
                            "발화 없이 입력이 종료되어 STT stream을 취소합니다.",
                            extra={"component": "voice", "event": "no_speech_cancelled"},
                        )
                        await self._finalize("CANCELLED")
                        self._enter_waiting_wake(clear_followup=True, clear_error=True)
                        return
                try:
                    event = event_wait.result()
                except StopAsyncIteration:
                    break
                event_type = getattr(event, "type", None)
                if event_type is VoiceRuntimeEventType.TRANSCRIPT:
                    saw_transcript = True
                    saw_processing = True
                    if playback_task is None and self._state is not VoiceState.PROCESSING:
                        self._transition(VoiceState.PROCESSING)
                    self._input_stop.set()
                    self._audio.set_accepting(False)
                    self._audio.discard_pending()
                    input_stopped = True
                elif event_type is VoiceRuntimeEventType.AUDIO:
                    # Realtime response audio and the separate input transcription are
                    # asynchronous. Audio is allowed to arrive first; the runtime holds
                    # TURN_ENDED until the final transcript has also arrived.
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
                    error_code = getattr(event, "error_code", None)
                    raise VoiceFatalError(
                        error_code if isinstance(error_code, str) else "voice_pipeline_failed"
                    )
                elif (
                    event_type is VoiceRuntimeEventType.LIFECYCLE
                    and getattr(event, "lifecycle", None)
                    is VoiceRuntimeLifecycle.SPEECH_STARTED
                ):
                    # 고정 RMS 문턱보다 provider VAD가 먼저 실제 발화를 확인할 수 있다.
                    # 그 경우 local speech-start timeout이 조용한 발화를 끊지 않게 한다.
                    speech_started.set()
                elif (
                    event_type is VoiceRuntimeEventType.LIFECYCLE
                    and getattr(event, "lifecycle", None)
                    is VoiceRuntimeLifecycle.PROCESSING_STARTED
                ):
                    saw_processing = True
                    if not input_stopped:
                        self._input_stop.set()
                        self._audio.set_accepting(False)
                        self._audio.discard_pending()
                        input_stopped = True
                    if playback_task is None and self._state is not VoiceState.PROCESSING:
                        self._transition(VoiceState.PROCESSING)
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

            # No final transcript is an empty/no-speech turn only when no response
            # started. Playing audio and then silently cancelling would truncate a
            # provider response and falsify the public state.
            if not saw_transcript:
                if playback_task is not None or audio_chunk_count:
                    raise VoiceFatalError("voice_pipeline_failed")
                await self._finalize("CANCELLED")
                self._enter_waiting_wake(clear_followup=True, clear_error=True)
                return
            if not saw_turn_ended:
                raise VoiceFatalError("voice_pipeline_failed")
            if audio_chunk_count == 0:
                # An audio-only Realtime session is not successful until at least one
                # audible PCM chunk exists. Never mark a silent response SUCCEEDED.
                raise VoiceFatalError("voice_response_audio_missing")
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
            await self._finalize("SUCCEEDED")
            # This guard remains inside the registered public turn task.  There is no
            # parent await between speaker drain and opening the follow-up window.
            self._audio.set_accepting(False)
            self._audio.discard_pending()
            await asyncio.sleep(self._settings.post_playback_guard_seconds)
            # AI의 request_followup 판단과 무관하게 항상 후속 발화 창을 연다.
            # 창 안에 발화가 없으면 아래 _wait_for_followup_candidate가 조용히 웨이크 대기로 돌린다.
            if self._settings.followup_enabled and not self._stopping:
                # Keep post-TTS room/speaker tail out of the follow-up pre-roll.
                # The hardware stream remains open, but frames are discarded while
                # accepting is false, so this is strictly half-duplex.
                self._audio.discard_pending()
                self._audio.set_accepting(True)
                self._open_followup_window()
            else:
                self._enter_waiting_wake(clear_followup=True, clear_error=True)
        finally:
            self._input_stop.set()
            wait_tasks = (event_wait, input_ended_wait, speech_started_wait)
            for task in wait_tasks:
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in wait_tasks if task is not None),
                return_exceptions=True,
            )
            # ERROR event를 받은 직후에는 다음 anext가 없다. Realtime async
            # generator를 명시적으로 닫아 socket/feeder finally를 즉시 실행한다.
            if runtime_iterator is not None:
                close_iterator = getattr(runtime_iterator, "aclose", None)
                if callable(close_iterator):
                    try:
                        await close_iterator()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        LOGGER.warning(
                            "Voice runtime stream을 닫지 못했습니다.",
                            extra={
                                "component": "voice",
                                "event": "runtime_stream_close_failed",
                                "error_code": "runtime_stream_close_failed",
                            },
                        )
            if playback_task is not None and not playback_task.done():
                playback_task.cancel()
            if playback_task is not None:
                await asyncio.gather(playback_task, return_exceptions=True)
            self._input_stop = None

    async def _audio_chunks(
        self,
        initial: tuple[AudioChunk, ...],
        *,
        speech_already_started: bool,
        speech_started_event: asyncio.Event | None = None,
    ) -> AsyncIterator[bytes]:
        """Yield original 24kHz PCM; RMS only ends the wait *for speech to start*."""
        speech_started = speech_already_started
        if speech_started and speech_started_event is not None:
            speech_started_event.set()
        deadline = None if speech_started else time.monotonic() + self._settings.speech_start_timeout_seconds
        for chunk in initial:
            yield chunk.pcm
        while not self._stopping and not (self._input_stop and self._input_stop.is_set()):
            # provider VAD의 SPEECH_STARTED도 local RMS와 동등한 발화 증거다.
            # 조용하지만 provider가 알아들은 긴 발화를 3초 local timeout으로
            # 자르지 않도록 외부 event를 내부 deadline에 반영한다.
            if (
                not speech_started
                and speech_started_event is not None
                and speech_started_event.is_set()
            ):
                speech_started = True
                deadline = None
            remaining = (
                None
                if speech_started
                else max(0.0, (deadline or 0.0) - time.monotonic())
            )
            if remaining is not None and remaining <= 0:
                return
            # 발화가 시작된 뒤에도 유한 timeout으로 read해야 callback 정지와
            # input_stop을 확인할 수 있다. 건강한 stream은 80ms마다 frame을 준다.
            timeout = (
                AUDIO_READ_POLL_SECONDS
                if remaining is None
                else min(AUDIO_READ_POLL_SECONDS, remaining)
            )
            try:
                chunk = await self._audio.read(timeout_seconds=timeout)
            except TimeoutError:
                if not speech_started and time.monotonic() >= (deadline or 0.0):
                    return
                continue
            yield chunk.pcm
            if not speech_started and calculate_rms(chunk.pcm) >= self._settings.silence_rms_threshold:
                speech_started = True
                if speech_started_event is not None:
                    speech_started_event.set()

    async def _wait_for_followup_candidate(self) -> tuple[AudioChunk, ...] | None:
        deadline = self._followup_deadline
        if deadline is None:
            raise VoiceFatalError("followup_deadline_missing")
        pre_roll: deque[AudioChunk] = deque(maxlen=ceil(self._settings.followup_preroll_seconds / INPUT_FRAME_SECONDS))
        # 마이크 노이즈 플로어가 임계값 근처에서 요동치는 환경에서는 단일 프레임
        # 스파이크만으로 turn을 열면 사용자가 말하기도 전에 창이 소진된다.
        # 연속으로 임계값을 넘는 프레임이 쌓여야 실제 발화로 인정한다.
        streak = 0
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                chunk = await self._audio.read(timeout_seconds=remaining)
            except TimeoutError:
                return None
            pre_roll.append(chunk)
            if calculate_rms(chunk.pcm) >= self._settings.silence_rms_threshold:
                streak += 1
                if streak >= self._settings.followup_speech_frames:
                    return tuple(pre_roll)
            else:
                streak = 0
        return None

    async def _finalize(self, outcome: str, *, error_code: str | None = None) -> None:
        finalize = getattr(self._runtime, "finalize_turn", None)
        if callable(finalize):
            await finalize(outcome, error_code=error_code)

    async def _recover_turn_error(self, code: str) -> None:
        self._audio.set_accepting(False)
        self._audio.discard_pending()
        try:
            await self._playback.play_effect(EffectName.ERROR)
        except asyncio.CancelledError:
            raise
        except VoiceFatalError as error:
            if error.code in RECOVERABLE_DEVICE_ERRORS:
                raise
            LOGGER.warning(
                "Voice 오류 효과음을 재생하지 못했습니다.",
                extra={
                    "component": "voice",
                    "event": "error_effect_failed",
                    "error_code": error.code,
                },
            )
        except Exception:
            LOGGER.warning(
                "Voice 오류 효과음을 재생하지 못했습니다.",
                extra={
                    "component": "voice",
                    "event": "error_effect_failed",
                    "error_code": "error_effect_failed",
                },
            )
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
        # 대기 밖에서 접수되었거나 stop으로 소비되지 못한 트리거는 여기서 버린다.
        self._manual_wake.clear()
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
