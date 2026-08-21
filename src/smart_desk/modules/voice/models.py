"""로컬 음성 pipeline이 공유하는 불변 모델과 상태를 정의한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import math


INPUT_SAMPLE_RATE = 24_000
INPUT_FRAME_SAMPLES = 1_920
PCM_SAMPLE_BYTES = 2
INPUT_FRAME_BYTES = INPUT_FRAME_SAMPLES * PCM_SAMPLE_BYTES
INPUT_FRAME_SECONDS = INPUT_FRAME_SAMPLES / INPUT_SAMPLE_RATE
OUTPUT_SAMPLE_RATE = 24_000


class VoiceFatalError(Exception):
    """현재 process에서 Voice를 계속 실행할 수 없는 오류다."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class AudioChunk:
    """microphone callback 한 번에서 얻은 고정 크기 PCM frame이다."""

    pcm: bytes
    captured_at: float

    def __post_init__(self) -> None:
        if len(self.pcm) != INPUT_FRAME_BYTES:
            raise ValueError(f"입력 PCM은 정확히 {INPUT_FRAME_BYTES} bytes여야 합니다.")
        if not math.isfinite(self.captured_at) or self.captured_at < 0:
            raise ValueError("audio capture 시각은 0 이상의 finite 값이어야 합니다.")


class EffectName(StrEnum):
    ACKNOWLEDGEMENT = "acknowledgement"
    ERROR = "error"


class VoiceState(StrEnum):
    DISABLED = "DISABLED"
    WAITING_WAKE = "WAITING_WAKE"
    WAITING_FOLLOWUP = "WAITING_FOLLOWUP"
    ACKNOWLEDGING = "ACKNOWLEDGING"
    RECORDING = "RECORDING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class VoiceSnapshot:
    """content를 포함하지 않는 Voice 공개 상태다."""

    state: VoiceState
    last_transition_at: datetime
    followup_expires_at: datetime | None
    last_error: str | None

    def __post_init__(self) -> None:
        for value in (self.last_transition_at, self.followup_expires_at):
            if value is not None and (
                value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value)
            ):
                raise ValueError("Voice snapshot 시각은 UTC-aware datetime이어야 합니다.")
        if self.last_error is not None and (
            not self.last_error or not self.last_error.replace("_", "").isalnum()
        ):
            raise ValueError("Voice 오류는 content-free code여야 합니다.")
