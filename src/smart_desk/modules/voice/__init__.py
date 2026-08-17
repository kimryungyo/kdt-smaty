"""Voice public surface (legacy exports remain until Task 02B cutover)."""

from smart_desk.modules.voice.models import VoiceSnapshot, VoiceState
from smart_desk.modules.voice.resample import WakeWordResampler
from smart_desk.modules.voice.service import VoiceService

__all__ = ["VoiceService", "VoiceSnapshot", "VoiceState", "WakeWordResampler"]
