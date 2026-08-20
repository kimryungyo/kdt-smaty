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


# Pi 5의 40핀 헤더에서 하드웨어 PWM으로 쓸 수 있는 핀과 그 채널 번호.
# dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4 기준이다.
_HARDWARE_PWM_CHANNELS = {12: 0, 13: 1}
_PWM_SYSFS_ROOT = Path("/sys/class/pwm")


class HardwarePwm:
    """sysfs 하드웨어 PWM 한 채널.

    lgpio의 소프트웨어 PWM은 최대 10kHz라 모터 구동에 쓰는 20kHz를 낼 수 없다.
    가청 대역을 벗어나려면 하드웨어 PWM이 필요하다.
    """

    def __init__(self, channel: int, frequency_hz: int, chip: str) -> None:
        self._root = _PWM_SYSFS_ROOT / chip / f"pwm{channel}"
        self._period_ns = int(1_000_000_000 / frequency_hz)
        if not self._root.exists():
            (_PWM_SYSFS_ROOT / chip / "export").write_text(str(channel))
        self._write("period", self._period_ns)
        self._write("duty_cycle", 0)
        self._write("enable", 1)

    def set_duty_percent(self, duty_percent: int) -> None:
        duty = max(0, min(100, duty_percent))
        self._write("duty_cycle", self._period_ns * duty // 100)

    def close(self) -> None:
        try:
            self._write("duty_cycle", 0)
            self._write("enable", 0)
        except OSError:
            pass

    def _write(self, name: str, value: int) -> None:
        (self._root / name).write_text(str(value))


def open_hardware_pwm(pin: int, frequency_hz: int) -> HardwarePwm | None:
    """해당 핀의 하드웨어 PWM을 연다. 쓸 수 없으면 None을 돌려준다.

    호출자는 None일 때 소프트웨어 PWM으로 물러선다. overlay가 없거나 sysfs에
    권한이 없는 환경에서도 기동은 되어야 하기 때문이다.
    """

    channel = _HARDWARE_PWM_CHANNELS.get(pin)
    if channel is None or not _PWM_SYSFS_ROOT.exists():
        return None
    for chip in sorted(entry.name for entry in _PWM_SYSFS_ROOT.iterdir()):
        try:
            return HardwarePwm(channel, frequency_hz, chip)
        except OSError:
            continue
    return None
