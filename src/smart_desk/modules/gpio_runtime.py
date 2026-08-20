"""lgpio import 경계.

lgpio는 import 시점에 알림 FIFO(`.lgd-nfy<N>`)를 **현재 작업 디렉터리**에 만든다.
컨테이너의 작업 디렉터리(/app)는 root 소유라 서비스 uid로 쓸 수 없어 import
자체가 FileNotFoundError로 실패한다. 쓰기 가능한 디렉터리로 잠깐 옮겨 import한
뒤 원래 위치로 돌아온다.

한 번 import된 모듈은 sys.modules에 남으므로 이 비용은 프로세스당 한 번이다.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any


_WRITABLE_CANDIDATES = ("/app/data", "/tmp")


def _writable_directory() -> str:
    for candidate in _WRITABLE_CANDIDATES:
        if os.access(candidate, os.W_OK):
            return candidate
    return tempfile.gettempdir()


@contextmanager
def _chdir(target: str) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(target)
    try:
        yield
    finally:
        os.chdir(previous)


def import_lgpio() -> Any:
    """쓰기 가능한 작업 디렉터리에서 lgpio를 import한다."""

    with _chdir(_writable_directory()):
        import lgpio

        return lgpio
