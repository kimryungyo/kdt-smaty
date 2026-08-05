"""테스트 사이의 설정과 container singleton을 격리한다."""

import pytest

from smart_desk.config.settings import reset_settings_cache
from smart_desk.core.container import reset_container


@pytest.fixture(autouse=True)
def isolate_singletons():
    """각 테스트 전후에 프로세스 전역 참조를 비운다."""

    reset_container()
    reset_settings_cache()
    yield
    reset_container()
    reset_settings_cache()

