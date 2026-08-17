"""AI 스피커 임시 디버그 API와 페이지 테스트."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

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


def make_client() -> TestClient:
    view = VoiceDebugView(
        voice=FakeVoice(),  # type: ignore[arg-type]
        wakeword=FakeWakeWord(),  # type: ignore[arg-type]
        audio_input=FakeAudioInput(),  # type: ignore[arg-type]
    )
    return TestClient(create_voice_debug_application(view))


def test_debug_snapshot_combines_voice_observability_without_provider_secret() -> None:
    response = make_client().get("/api/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["voice"]["state"] == "WAITING_FOLLOWUP"
    assert payload["wakeword"]["score"] == 0.73
    assert payload["wakeword"]["inference_p95_ms"] == 31.0
    assert payload["audio_input"]["queue_capacity"] == 64
    assert payload["audio_input"]["estimated_snr_db"] == 23.2
    assert "assistant" not in payload
    assert "encrypted_content" not in response.text
    assert "api_key" not in response.text


def test_debug_page_is_no_store_and_polls_snapshot() -> None:
    response = make_client().get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "AI Speaker Debug" in response.text
    assert "/api/snapshot" in response.text
    assert "setInterval(refresh,50)" in response.text
    assert "noise floor (est.)" in response.text
    assert "Wake Word telemetry" in response.text
