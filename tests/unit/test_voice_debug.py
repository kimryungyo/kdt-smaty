"""AI 스피커 임시 디버그 API와 페이지 테스트."""

from datetime import datetime, timezone

import httpx

from smart_desk.modules.voice.audio import AudioInputDebugSnapshot
from smart_desk.modules.voice.debug import VoiceDebugView, create_voice_debug_application
from smart_desk.modules.voice.models import VoiceSnapshot, VoiceState
from smart_desk.modules.voice.wakeword import WakeWordDebugSnapshot
from smart_desk.modules.assistant.turns import AssistantTurn, TurnPhase, TurnStatus


NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)


class FakeVoice:
    def __init__(self, *, wake_accepted: bool = True) -> None:
        self.wake_accepted = wake_accepted
        self.wake_calls = 0

    def get_snapshot(self) -> VoiceSnapshot:
        return VoiceSnapshot(
            state=VoiceState.WAITING_FOLLOWUP,
            last_transition_at=NOW,
            followup_expires_at=NOW,
            last_error=None,
        )

    def trigger_wake(self) -> bool:
        self.wake_calls += 1
        return self.wake_accepted


class FakeWakeWord:
    def get_debug_snapshot(self) -> WakeWordDebugSnapshot:
        return WakeWordDebugSnapshot(
            model="hi_smarty_ko",
            score=0.73,
            threshold=0.13,
            activation_streak=2,
            consecutive_frames=2,
            armed=False,
            recent_max_score=0.81,
            inference_count=25,
            last_inference_ms=22.5,
            inference_p50_ms=21.0,
            inference_p95_ms=31.0,
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
            latest_rms_dbfs=-24.1,
            latest_peak_dbfs=-8.2,
            recent_peak_dbfs=-6.9,
            estimated_noise_floor_dbfs=-47.3,
            estimated_snr_db=23.2,
            latest_clipping_ratio=0.0,
            clipped_frames=0,
            signal_frames=125,
            latest_dc_offset_pcm=12.4,
        )


class FakeAssistantTurns:
    async def latest(self) -> AssistantTurn:
        return AssistantTurn(
            turn_id="turn-test",
            session_id=None,
            profile_id=None,
            phase=TurnPhase.FINAL,
            sequence=3,
            status=TurnStatus.SUCCEEDED,
            title="음성 요청",
            summary="책상을 10cm 올렸습니다.",
            detail=None,
            started_at=NOW,
            updated_at=NOW,
            completed_at=NOW,
        )


def make_view(voice: FakeVoice | None = None) -> VoiceDebugView:
    return VoiceDebugView(
        voice=voice or FakeVoice(),  # type: ignore[arg-type]
        wakeword=FakeWakeWord(),  # type: ignore[arg-type]
        audio_input=FakeAudioInput(),  # type: ignore[arg-type]
        assistant_turns=FakeAssistantTurns(),  # type: ignore[arg-type]
    )


def make_application():
    return create_voice_debug_application(make_view())


async def test_debug_snapshot_combines_voice_observability_without_provider_secret() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=make_application()), base_url="http://test"
    ) as client:
        response = await client.get("/api/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"voice", "wakeword", "audio_input", "assistant_response"}
    assert payload["voice"]["state"] == "WAITING_FOLLOWUP"
    assert payload["wakeword"]["score"] == 0.73
    assert payload["wakeword"]["inference_p95_ms"] == 31.0
    assert payload["audio_input"]["queue_capacity"] == 64
    assert payload["audio_input"]["estimated_snr_db"] == 23.2
    assert payload["assistant_response"]["text"] == "책상을 10cm 올렸습니다."
    assert payload["assistant_response"]["status"] == "SUCCEEDED"
    assert "transcript" not in response.text.lower()
    assert "api_key" not in response.text.lower()


async def test_wake_endpoint_forwards_to_voice_and_reports_result() -> None:
    voice = FakeVoice(wake_accepted=True)
    app = create_voice_debug_application(make_view(voice))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/wake")

    assert response.status_code == 200
    assert voice.wake_calls == 1
    payload = response.json()
    assert payload["accepted"] is True
    assert payload["state"] == "WAITING_FOLLOWUP"


async def test_wake_endpoint_reports_ignored_trigger() -> None:
    voice = FakeVoice(wake_accepted=False)
    app = create_voice_debug_application(make_view(voice))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/wake")

    assert response.status_code == 200
    assert response.json()["accepted"] is False


async def test_debug_page_is_no_store_and_polls_snapshot() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=make_application()), base_url="http://test"
    ) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "AI Speaker Debug" in response.text
    assert 'id="dashboardLink"' in response.text
    assert 'id="visionLink"' in response.text
    assert "/api/snapshot" in response.text
    assert "setInterval(refresh,250)" in response.text
    assert "noise floor (est.)" in response.text
    assert "Wake Word telemetry" in response.text
    assert "data.assistant_response" in response.text
    assert 'id="assistantResponse"' in response.text
    assert 'id="session"' not in response.text
    assert 'id="turns"' not in response.text
