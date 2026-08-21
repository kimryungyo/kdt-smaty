"""효과음과 streaming PCM 출력 직렬화 테스트."""

import asyncio
from pathlib import Path
import wave

import pytest

from smart_desk.modules.voice.models import EffectName, VoiceFatalError
from smart_desk.modules.voice.playback import PlaybackCoordinator


class FakePcmOutput:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.writes: list[bytes] = []

    async def start(self) -> None:
        self.events.append("start")

    async def write(self, pcm: bytes) -> None:
        self.events.append("write")
        self.writes.append(pcm)

    async def drain(self) -> None:
        self.events.append("drain")

    async def abort(self) -> None:
        self.events.append("abort")

    async def stop(self) -> None:
        self.events.append("stop")


def write_effect(path: Path, *, frames: int = 480) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24_000)
        wav_file.writeframes(b"\1\0" * frames)


def make_playback(tmp_path: Path) -> tuple[PlaybackCoordinator, FakePcmOutput]:
    acknowledgement = tmp_path / "ack.wav"
    error = tmp_path / "error.wav"
    write_effect(acknowledgement)
    write_effect(error)
    output = FakePcmOutput()
    return (
        PlaybackCoordinator(
            output,
            acknowledgement_effect_path=acknowledgement,
            error_effect_path=error,
        ),
        output,
    )


async def chunks(*values: bytes):
    for value in values:
        yield value


async def test_effect_and_speech_are_written_and_drained(tmp_path: Path) -> None:
    playback, output = make_playback(tmp_path)
    await playback.start()

    await playback.play_effect(EffectName.ACKNOWLEDGEMENT)
    await playback.play_speech(chunks(b"\x01", b"\x02\x03", b"\x04"))
    await playback.stop()

    assert output.writes[1:] == [b"\x01\x02", b"\x03\x04"]
    assert output.events == [
        "start",
        "write",
        "drain",
        "write",
        "write",
        "drain",
        "abort",
        "stop",
    ]


async def test_odd_final_pcm_byte_aborts_output(tmp_path: Path) -> None:
    playback, output = make_playback(tmp_path)
    await playback.start()

    with pytest.raises(ValueError, match="불완전"):
        await playback.play_speech(chunks(b"\x01"))

    assert output.events[-1] == "abort"


async def test_partial_stream_failure_aborts_output(tmp_path: Path) -> None:
    playback, output = make_playback(tmp_path)
    await playback.start()

    async def failing_chunks():
        yield b"\x01\x02"
        raise RuntimeError("provider failed")

    with pytest.raises(RuntimeError, match="provider failed"):
        await playback.play_speech(failing_chunks())

    assert output.events[-1] == "abort"


async def test_empty_speech_is_not_reported_as_success(tmp_path: Path) -> None:
    playback, output = make_playback(tmp_path)
    await playback.start()

    with pytest.raises(VoiceFatalError, match="voice_response_audio_missing"):
        await playback.play_speech(chunks())

    assert output.events[-1] == "abort"


async def test_effect_failure_aborts_output(tmp_path: Path) -> None:
    playback, output = make_playback(tmp_path)
    await playback.start()

    async def fail_drain() -> None:
        raise VoiceFatalError("speaker_failed")

    output.drain = fail_drain  # type: ignore[method-assign]
    with pytest.raises(VoiceFatalError, match="speaker_failed"):
        await playback.play_effect(EffectName.ACKNOWLEDGEMENT)

    assert output.events[-1] == "abort"


async def test_stop_cancels_current_speech_and_is_idempotent(tmp_path: Path) -> None:
    playback, output = make_playback(tmp_path)
    await playback.start()

    async def waiting_chunks():
        yield b"\x01\x02"
        await asyncio.Future()

    speech = asyncio.create_task(playback.play_speech(waiting_chunks()))
    await asyncio.sleep(0)
    await playback.stop()
    await playback.stop()

    assert speech.cancelled()
    assert output.events.count("stop") == 1


async def test_invalid_effect_format_fails_start(tmp_path: Path) -> None:
    acknowledgement = tmp_path / "ack.wav"
    error = tmp_path / "error.wav"
    write_effect(acknowledgement, frames=4_000)
    write_effect(error)
    playback = PlaybackCoordinator(
        FakePcmOutput(),
        acknowledgement_effect_path=acknowledgement,
        error_effect_path=error,
    )

    with pytest.raises(VoiceFatalError, match="voice_effect_invalid"):
        await playback.start()
