"""명시적으로 opt-in한 OpenAI Realtime 연결 smoke test.

실제 모델 연결은 비용과 API 권한을 사용하므로 CI와 기본 pytest에서는 절대 실행하지 않는다.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from smart_desk.modules.assistant.realtime_runtime import (
    OpenAIWebSocketTransport,
    RealtimeVoiceConfig,
    RealtimeVoiceRuntime,
)


@pytest.mark.openai_voice_integration
async def test_openai_realtime_session_accepts_configuration() -> None:
    if os.getenv("SMART_DESK_RUN_OPENAI_INTEGRATION") != "1":
        pytest.skip("SMART_DESK_RUN_OPENAI_INTEGRATION=1에서만 실제 OpenAI 연결을 실행합니다.")
    api_key = os.getenv("SMART_DESK_OPENAI__API_KEY")
    if not api_key:
        pytest.skip("SMART_DESK_OPENAI__API_KEY가 필요합니다.")
    model = os.getenv("SMART_DESK_OPENAI__REALTIME_MODEL", "gpt-realtime-2.1")
    transport = await OpenAIWebSocketTransport.connect(api_key=api_key, model=model)
    try:
        created = await asyncio.wait_for(transport.receive_json(), timeout=10)
        assert created["type"] == "session.created"
        runtime = RealtimeVoiceRuntime(
            lambda: (_ for _ in ()).throw(AssertionError("transport is already connected")),
            lambda *_: (_ for _ in ()).throw(AssertionError("tool is not used")),
            config=RealtimeVoiceConfig(model=model),
        )
        await transport.send_json(runtime._session_update())  # noqa: SLF001 - wire-format contract
        event = await asyncio.wait_for(transport.receive_json(), timeout=10)
        assert event["type"] == "session.updated"
    finally:
        await transport.close()
