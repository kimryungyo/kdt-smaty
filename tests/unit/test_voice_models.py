"""Voice 상태와 audio DTO 검증 테스트."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from smart_desk.modules.assistant.models import (
    AssistantDecisionReason,
    AssistantNextAction,
    AssistantReply,
    OpenAiTurn,
)
from smart_desk.modules.voice.models import (
    AudioChunk,
    AudioUtterance,
    INPUT_FRAME_BYTES,
    VoiceSnapshot,
    VoiceState,
)


def test_audio_chunk_requires_exact_pcm_and_monotonic_timestamp() -> None:
    chunk = AudioChunk(pcm=b"\0" * INPUT_FRAME_BYTES, captured_at=1.25)

    assert len(chunk.pcm) == INPUT_FRAME_BYTES

    with pytest.raises(ValueError, match=str(INPUT_FRAME_BYTES)):
        AudioChunk(pcm=b"\0" * 2, captured_at=1.25)
    with pytest.raises(ValueError, match="finite"):
        AudioChunk(pcm=b"\0" * INPUT_FRAME_BYTES, captured_at=float("nan"))


def test_audio_utterance_rejects_raw_pcm() -> None:
    with pytest.raises(ValueError, match="RIFF/WAVE"):
        AudioUtterance(wav=b"not-wav", duration_seconds=0.1)


def test_voice_snapshot_is_frozen_and_requires_utc_time() -> None:
    snapshot = VoiceSnapshot(
        state=VoiceState.WAITING_WAKE,
        last_transition_at=datetime.now(timezone.utc),
        followup_expires_at=None,
        last_error=None,
    )

    with pytest.raises(AttributeError):
        snapshot.state = VoiceState.ERROR  # type: ignore[misc]
    with pytest.raises(ValueError, match="UTC-aware"):
        VoiceSnapshot(
            state=VoiceState.ERROR,
            last_transition_at=datetime.now(),
            followup_expires_at=None,
            last_error="speaker_failed",
        )


def test_voice_snapshot_rejects_exception_text_as_error_code() -> None:
    with pytest.raises(ValueError, match="content-free"):
        VoiceSnapshot(
            state=VoiceState.ERROR,
            last_transition_at=datetime.now(timezone.utc),
            followup_expires_at=None,
            last_error="API key=secret!",
        )


def test_assistant_reply_is_frozen_single_paragraph_and_forbids_extra() -> None:
    reply = AssistantReply(
        spoken_text="  짧은 답변입니다.  ",
        next_action=AssistantNextAction.RETURN_TO_WAKE_WORD,
        decision_reason=AssistantDecisionReason.CONVERSATION_COMPLETE,
    )

    assert reply.spoken_text == "짧은 답변입니다."
    with pytest.raises(ValidationError):
        AssistantReply(spoken_text="첫 줄\n둘째 줄", next_action="RETURN_TO_WAKE_WORD", decision_reason="CONVERSATION_COMPLETE")
    with pytest.raises(ValidationError):
        AssistantReply(spoken_text="가" * 241, next_action="RETURN_TO_WAKE_WORD", decision_reason="CONVERSATION_COMPLETE")
    with pytest.raises(ValidationError):
        AssistantReply(spoken_text="답변", next_action="RETURN_TO_WAKE_WORD", decision_reason="CONVERSATION_COMPLETE", unexpected=True)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        AssistantReply(spoken_text="답변", decision_reason="CONVERSATION_COMPLETE")
    with pytest.raises(ValidationError):
        AssistantReply(spoken_text="답변", next_action="RETURN_TO_WAKE_WORD")


def test_openai_turn_rejects_negative_token_count() -> None:
    with pytest.raises(ValueError, match="음수"):
        OpenAiTurn(
            reply=AssistantReply(spoken_text="답변", next_action="RETURN_TO_WAKE_WORD", decision_reason="CONVERSATION_COMPLETE"),
            output_items=(),
            request_id=None,
            input_tokens=-1,
            output_tokens=None,
        )
