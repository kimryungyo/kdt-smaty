"""먼저 말하기용 OpenAI TTS client 수명주기."""

from smart_desk.modules.voice.speech import OpenAiSpeechSynthesizer


class Client:
    def __init__(self) -> None:
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


async def test_injected_client_is_not_closed_by_synthesizer() -> None:
    client = Client()
    synthesizer = OpenAiSpeechSynthesizer(api_key="", client=client)

    await synthesizer.stop()

    assert client.closed == 0


async def test_owned_client_is_closed_and_stop_is_idempotent() -> None:
    client = Client()
    synthesizer = OpenAiSpeechSynthesizer(api_key="key")
    synthesizer._client = client  # type: ignore[assignment]  # noqa: SLF001

    await synthesizer.stop()
    await synthesizer.stop()

    assert client.closed == 1
