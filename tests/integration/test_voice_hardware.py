"""명시적으로 opt-in한 local microphone/speaker open과 effect 재생 test."""

from pathlib import Path

import pytest

from smart_desk.config.settings import Settings
from smart_desk.modules.voice.audio import LocalAudioInput, LocalPcmOutput
from smart_desk.modules.voice.models import EffectName
from smart_desk.modules.voice.playback import PlaybackCoordinator


pytestmark = pytest.mark.voice_hardware


async def test_local_audio_devices_open_and_play_effect() -> None:
    settings = Settings(openai={"api_key": "hardware-test-not-used"}, _env_file=None)
    audio_input = LocalAudioInput(
        device_name=settings.voice.input_device_name,
        queue_frames=settings.voice.input_queue_frames,
    )
    playback = PlaybackCoordinator(
        LocalPcmOutput(device_name=settings.voice.output_device_name),
        acknowledgement_effect_path=Path(
            settings.voice.acknowledgement_effect_path
        ),
        error_effect_path=Path(settings.voice.error_effect_path),
    )

    await playback.start()
    try:
        await audio_input.start()
        try:
            await playback.play_effect(EffectName.ACKNOWLEDGEMENT)
        finally:
            await audio_input.stop()
    finally:
        await playback.stop()
