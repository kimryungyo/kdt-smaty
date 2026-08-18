"""상태 없는 7-segment JSON 높이 변환 테스트."""

from __future__ import annotations

import json

import pytest

from smart_desk.config.settings import DeskSettings
from smart_desk.modules.desk.segment import MASK_TO_DIGIT, SegmentDecoder


DIGIT_TO_MASK = {digit: mask for mask, digit in MASK_TO_DIGIT.items()}


def frame(
    digits: str,
    *,
    point_after: int | None = None,
    fresh: object = 7,
) -> dict[str, object]:
    assert len(digits) == 3
    packet: dict[str, object] = {"fresh": fresh}
    for index, number in enumerate((8, 9, 10)):
        packet[f"m{number}"] = DIGIT_TO_MASK[digits[index]]
        packet[f"p{number}"] = int(point_after == number)
    return packet


def encoded_height(
    digits: str,
    *,
    point_after: int | None = None,
    as_bytes: bool = False,
) -> bytes | str:
    message = json.dumps(frame(digits, point_after=point_after))
    return message.encode() if as_bytes else message


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (encoded_height("730", point_after=9), 73.0),
        (encoded_height("750", point_after=9, as_bytes=True), 75.0),
        (encoded_height("802", point_after=9), 80.2),
        (encoded_height("118"), 118.0),
    ],
)
def test_valid_complete_frames_are_decoded(
    message: bytes | str,
    expected: float,
) -> None:
    assert SegmentDecoder(DeskSettings()).decode(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        b"\xff",
        "",
        "{",
        "[]",
        '{"status":"reader_started"}',
        '{"height_cm":80.2}',
        80.2,
    ],
)
def test_invalid_encoding_json_or_shape_is_rejected(message: object) -> None:
    assert SegmentDecoder(DeskSettings()).decode(message) is None  # type: ignore[arg-type]


@pytest.mark.parametrize("fresh", [None, True, False, -1, 0, 1, 2, 3, 4, 5, 6, 8, 7.0, "7"])
def test_only_plain_integer_fresh_seven_is_accepted(fresh: object) -> None:
    packet = frame("802", point_after=9, fresh=fresh)

    assert SegmentDecoder(DeskSettings()).decode(json.dumps(packet)) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("m8", True),
        ("m8", -1),
        ("m8", 127 + 1),
        ("m8", 0),
        ("m9", None),
        ("p8", True),
        ("p8", False),
        ("p9", 2),
        ("p10", -1),
        ("p10", 1.0),
    ],
)
def test_invalid_mask_and_point_values_are_rejected(
    field: str,
    value: object,
) -> None:
    packet = frame("802", point_after=9)
    packet[field] = value

    assert SegmentDecoder(DeskSettings()).decode(json.dumps(packet)) is None


@pytest.mark.parametrize("missing", ["m8", "m9", "m10", "p8", "p9", "p10", "fresh"])
def test_all_mask_point_and_fresh_fields_are_required(missing: str) -> None:
    packet = frame("802", point_after=9)
    del packet[missing]

    assert SegmentDecoder(DeskSettings()).decode(json.dumps(packet)) is None


def test_multiple_or_trailing_decimal_points_are_rejected() -> None:
    decoder = SegmentDecoder(DeskSettings())
    multiple = frame("802", point_after=8)
    multiple["p9"] = 1
    trailing = frame("118", point_after=10)

    assert decoder.decode(json.dumps(multiple)) is None
    assert decoder.decode(json.dumps(trailing)) is None


@pytest.mark.parametrize(
    "message",
    [
        encoded_height("729", point_after=9),
        encoded_height("119"),
    ],
)
def test_physical_range_is_enforced(message: bytes | str) -> None:
    assert SegmentDecoder(DeskSettings()).decode(message) is None


def test_partial_frames_are_not_combined_between_calls() -> None:
    decoder = SegmentDecoder(DeskSettings())
    first = frame("750", point_after=9, fresh=4)
    second = frame("802", point_after=9, fresh=3)

    assert decoder.decode(json.dumps(first)) is None
    assert decoder.decode(json.dumps(second)) is None


def test_reset_display_is_detected_but_never_decoded_as_height() -> None:
    packet = {"m8": 0x05, "p8": 0, "m9": 0x53, "p9": 0, "m10": 0x0F, "p10": 0, "fresh": 7}
    decoder = SegmentDecoder(DeskSettings())

    assert decoder.is_reset_display(json.dumps(packet)) is True
    assert decoder.decode(json.dumps(packet)) is None
