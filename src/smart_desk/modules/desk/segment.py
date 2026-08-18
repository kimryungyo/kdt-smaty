"""Arduino 7-segment mask JSON 한 줄을 책상 높이로 변환한다."""

from __future__ import annotations

import json
import math
from typing import Any

from smart_desk.config.settings import DeskSettings


MASK_TO_DIGIT = {
    0x7E: "0",
    0x30: "1",
    0x6D: "2",
    0x79: "3",
    0x33: "4",
    0x5B: "5",
    0x5F: "6",
    0x70: "7",
    0x7F: "8",
    0x7B: "9",
}
DIGIT_NUMBERS = (8, 9, 10)
REQUIRED_FIELDS = frozenset(
    (
        "fresh",
        *(f"m{number}" for number in DIGIT_NUMBERS),
        *(f"p{number}" for number in DIGIT_NUMBERS),
    )
)
RESET_DISPLAY = {"m8": 0x05, "p8": 0, "m9": 0x53, "p9": 0, "m10": 0x0F, "p10": 0}


class SegmentDecoder:
    """완성된 최신 mask frame만 상태 없이 유효 높이로 변환한다."""

    def __init__(self, settings: DeskSettings) -> None:
        self._settings = settings

    def decode(self, raw_message: bytes | str) -> float | None:
        """frame 형식과 물리 범위가 유효하면 높이를 반환한다."""

        text = self._decode_text(raw_message)
        if text is None:
            return None
        try:
            packet = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(packet, dict) or not REQUIRED_FIELDS.issubset(packet):
            return None

        fresh = packet.get("fresh")
        if not self._is_plain_int(fresh) or fresh != 7:
            return None

        digits: list[str] = []
        point_count = 0
        for number in DIGIT_NUMBERS:
            mask = packet.get(f"m{number}")
            point = packet.get(f"p{number}")
            if not self._is_plain_int(mask) or mask not in MASK_TO_DIGIT:
                return None
            if not self._is_plain_int(point) or point not in (0, 1):
                return None
            digits.append(MASK_TO_DIGIT[mask])
            if point == 1:
                point_count += 1
                if point_count > 1 or number == DIGIT_NUMBERS[-1]:
                    return None
                digits.append(".")

        try:
            height = float("".join(digits))
        except ValueError:
            return None
        if not math.isfinite(height):
            return None
        if not (
            self._settings.measurement_min_cm
            <= height
            <= self._settings.measurement_max_cm
        ):
            return None
        return height

    def is_reset_display(self, raw_message: bytes | str) -> bool:
        """Return whether a fully fresh ``rSt`` panel-reset frame was observed."""

        text = self._decode_text(raw_message)
        if text is None:
            return False
        try:
            packet = json.loads(text)
        except json.JSONDecodeError:
            return False
        return (isinstance(packet, dict) and packet.get("fresh") == 7
                and all(packet.get(key) == value for key, value in RESET_DISPLAY.items()))

    @staticmethod
    def _decode_text(raw_message: bytes | str) -> str | None:
        if isinstance(raw_message, bytes):
            try:
                return raw_message.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                return None
        if isinstance(raw_message, str):
            return raw_message
        return None

    @staticmethod
    def _is_plain_int(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)
