"""AI 스피커 임시 디버그 API와 페이지 테스트."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from smart_desk.modules.assistant.models import (
    AssistantDebugSnapshot,
    AssistantDebugTurn,
)
from smart_desk.modules.voice.audio import AudioInputDebugSnapshot
from smart_desk.modules.voice.debug import VoiceDebugView, create_voice_debug_application
from smart_desk.modules.voice.models import VoiceSnapshot, VoiceState
from smart_desk.modules.voice.wakeword import WakeWordDebugSnapshot


NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)


class FakeVoice:
    def get_snapshot(self) -> VoiceSnapshot:
        return VoiceSnapshot(
            state=VoiceState.WAITING_FOLLOWUP,
            last_transition_at=NOW,
            followup_expires_at=NOW,
            last_error=None,
        )


class FakeWakeWord:
    def get_debug_snapshot(self) -> WakeWordDebugSnapshot:
        return WakeWordDebugSnapshot(
            model="hey_jarvis",
            score=0.73,
            threshold=0.5,
            activation_streak=2,
            consecutive_frames=2,
            armed=False,
        )


class FakeAudioInput:
    def get_debug_snapshot(self) -> AudioInputDebugSnapshot:
        return AudioInputDebugSnapshot(
            accepting=True,
            queue_size=1,
            queue_capacity=64,
            dropped_frames=2,
            overflow_frames=3,
            callback_errors=4,
        )


class FakeAssistant:
    def get_debug_snapshot(self) -> AssistantDebugSnapshot:
        return AssistantDebugSnapshot(
            session_id="voice:local",
            completed_turns=1,
            history_items=3,
            history_item_types=("user", "reasoning", "message"),
            turns=(
                AssistantDebugTurn(
                    completed_at=NOW,
                    user_text="오늘 날씨는 어때?",
                    spoken_text="맑은 날씨입니다.",
                    request_id="req-debug",
                    input_tokens=10,
                    output_tokens=5,
                    output_item_types=("reasoning", "message"),
                ),
            ),
        )


def make_client() -> TestClient:
    view = VoiceDebugView(
        voice=FakeVoice(),  # type: ignore[arg-type]
        wakeword=FakeWakeWord(),  # type: ignore[arg-type]
        audio_input=FakeAudioInput(),  # type: ignore[arg-type]
        assistant=FakeAssistant(),  # type: ignore[arg-type]
    )
    return TestClient(create_voice_debug_application(view))


def test_debug_snapshot_combines_voice_observability_without_provider_secret() -> None:
    response = make_client().get("/api/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["voice"]["state"] == "WAITING_FOLLOWUP"
    assert payload["wakeword"]["score"] == 0.73
    assert payload["audio_input"]["queue_capacity"] == 64
    assert payload["assistant"]["turns"][0]["spoken_text"] == "맑은 날씨입니다."
    assert "encrypted_content" not in response.text
    assert "api_key" not in response.text


def test_debug_page_is_no_store_and_polls_snapshot() -> None:
    response = make_client().get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "AI Speaker Debug" in response.text
    assert "/api/snapshot" in response.text
