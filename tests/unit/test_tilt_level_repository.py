"""TiltLevelRepository의 fail-closed 보정 파일 검증 테스트."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from smart_desk.modules.tilt.level_repository import (
    TiltConfigurationError,
    TiltLevelRepository,
)


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def write_valid_files(tmp_path: Path) -> tuple[Path, Path]:
    levels = tmp_path / "levels.json"
    calibration = tmp_path / "calibration.json"
    write_json(levels, {"0": 0.0, "1": 38.0})
    write_json(
        calibration,
        {
            "100": {
                "UP": [{"speed_mm_s": 15.0, "at": 1.0}],
                "DOWN": [{"speed_mm_s": 19.0, "at": 1.0}],
            }
        },
    )
    return levels, calibration


def test_valid_files_are_loaded_and_validated(tmp_path: Path) -> None:
    levels, calibration = write_valid_files(tmp_path)

    repository = TiltLevelRepository(levels, calibration)
    repository.validate_for(min_level=0, max_level=1, move_duty_percent=100)

    assert repository.target_mm_for_level(1) == 38.0
    assert repository.calibration_snapshot() == [
        (100, "DOWN", 19.0),
        (100, "UP", 15.0),
    ]


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("levels.json", "{", "JSON"),
        ("levels.json", "[]", "최상위"),
        ("levels.json", '{"0": 0, "0": 1}', "중복"),
        ("levels.json", '{"01": 0}', "key 형식"),
        ("levels.json", '{"0": true}', "숫자"),
        ("levels.json", '{"0": "NaN"}', "숫자"),
    ],
)
def test_malformed_level_file_is_rejected(
    tmp_path: Path,
    filename: str,
    content: str,
    message: str,
) -> None:
    levels, calibration = write_valid_files(tmp_path)
    target = levels if filename == "levels.json" else calibration
    target.write_text(content, encoding="utf-8")

    with pytest.raises(TiltConfigurationError, match=message):
        TiltLevelRepository(levels, calibration)


@pytest.mark.parametrize(
    "levels_data",
    [
        {"0": 0.0},
        {"0": 0.0, "1": 0.0},
        {"0": 38.0, "1": 0.0},
        {"0": 0.0, "1": 38.0, "2": 73.0},
        {"0": -1.0, "1": 38.0},
    ],
)
def test_level_range_and_order_must_match_settings(tmp_path: Path, levels_data: dict[str, float]) -> None:
    levels, calibration = write_valid_files(tmp_path)
    write_json(levels, levels_data)
    repository = TiltLevelRepository(levels, calibration)

    with pytest.raises(TiltConfigurationError):
        repository.validate_for(min_level=0, max_level=1, move_duty_percent=100)


@pytest.mark.parametrize(
    "calibration_data",
    [
        {},
        {"0": {"UP": [{"speed_mm_s": 1}], "DOWN": [{"speed_mm_s": 1}]}},
        {"100": {"UP": [{"speed_mm_s": 1}]}},
        {"100": {"UP": [], "DOWN": [{"speed_mm_s": 1}]}},
        {"100": {"UP": [{"speed_mm_s": 0}], "DOWN": [{"speed_mm_s": 1}]}},
        {"100": {"UP": [{"speed_mm_s": True}], "DOWN": [{"speed_mm_s": 1}]}},
        {"100": {"UP": [{"speed_mm_s": 1, "unexpected": 1}], "DOWN": [{"speed_mm_s": 1}]}},
    ],
)
def test_invalid_calibration_is_rejected(tmp_path: Path, calibration_data: dict) -> None:
    levels, calibration = write_valid_files(tmp_path)
    write_json(calibration, calibration_data)

    with pytest.raises(TiltConfigurationError):
        TiltLevelRepository(levels, calibration)


def test_selected_duty_must_have_both_direction_calibrations(tmp_path: Path) -> None:
    levels, calibration = write_valid_files(tmp_path)
    repository = TiltLevelRepository(levels, calibration)

    with pytest.raises(TiltConfigurationError, match="duty 90"):
        repository.validate_for(min_level=0, max_level=1, move_duty_percent=90)


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    _, calibration = write_valid_files(tmp_path)

    with pytest.raises(TiltConfigurationError, match="파일이 없습니다"):
        TiltLevelRepository(tmp_path / "missing.json", calibration)
