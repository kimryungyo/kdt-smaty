from datetime import datetime, timezone

from smart_desk.modules.voice.models import VoiceSnapshot, VoiceState


def test_voice_snapshot_is_content_free():
    snapshot = VoiceSnapshot(VoiceState.WAITING_WAKE, datetime.now(timezone.utc), None, None)
    assert snapshot.state is VoiceState.WAITING_WAKE
