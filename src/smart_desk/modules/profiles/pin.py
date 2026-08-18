"""프로필 잠금 PIN의 해시 생성과 검증을 담당한다.

PIN은 4자리 숫자여서 경우의 수가 10,000개뿐이다. 이 잠금은 같은 집·사무실
안에서 남이 프로필을 실수로 바꾸거나 지우지 못하게 막는 용도이며, 네트워크
공격자를 막는 인증 수단이 아니다. 그래도 평문 저장은 하지 않고 profile마다
다른 salt와 PBKDF2로 저장한다.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets


PIN_PATTERN = re.compile(r"^[0-9]{4}$")
_ALGORITHM = "pbkdf2_sha256"
_HASH_NAME = "sha256"
_ITERATIONS = 200_000
_SALT_BYTES = 16


class InvalidPinFormatError(ValueError):
    """PIN이 4자리 숫자 형식이 아니다."""


def validate_pin(pin: str) -> str:
    """저장·검증 전에 PIN 형식을 확인한다."""

    if PIN_PATTERN.fullmatch(pin) is None:
        raise InvalidPinFormatError("PIN은 숫자 4자리여야 합니다.")
    return pin


def hash_pin(pin: str, *, iterations: int = _ITERATIONS) -> str:
    """저장용 PIN 해시 문자열을 만든다."""

    validate_pin(pin)
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _derive(pin, salt, iterations)
    return f"{_ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_pin(pin: str, stored: str | None) -> bool:
    """저장된 해시와 입력 PIN이 같은지 상수 시간으로 비교한다."""

    if stored is None or PIN_PATTERN.fullmatch(pin) is None:
        return False
    try:
        algorithm, raw_iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        iterations = int(raw_iterations)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(_derive(pin, salt, iterations), expected)


def _derive(pin: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac(_HASH_NAME, pin.encode("utf-8"), salt, iterations)
