"""테스트 사이의 설정과 container singleton을 격리한다."""

import importlib.util
import os

import pytest

from smart_desk.config.settings import reset_settings_cache
from smart_desk.core.container import reset_container


def pytest_ignore_collect(collection_path, config):  # type: ignore[no-untyped-def]
    """opt-in Voice test는 환경변수가 없으면 기본 collection에서 제외한다."""

    del config
    name = collection_path.name
    if name == "test_voice_hardware.py":
        return os.getenv("SMART_DESK_RUN_VOICE_HARDWARE") != "1"
    if name == "test_wakeword_builtin.py":
        return (
            importlib.util.find_spec("livekit") is None
            or importlib.util.find_spec("livekit.wakeword") is None
        )
    return None


@pytest.fixture(autouse=True)
def isolate_singletons():
    """각 테스트 전후에 프로세스 전역 참조를 비운다."""

    reset_container()
    reset_settings_cache()
    yield
    reset_container()
    reset_settings_cache()
