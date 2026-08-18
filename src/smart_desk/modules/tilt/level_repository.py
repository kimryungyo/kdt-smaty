"""틸팅 단계 목표와 속도 보정 파일을 fail-closed로 읽는다."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


DIRECTIONS = ("UP", "DOWN")


class TiltConfigurationError(ValueError):
    """틸트 장치를 움직이기에 부족하거나 잘못된 정적 설정이다."""


class _JsonObjectPairs(list[tuple[str, Any]]):
    """JSON array와 빈 object를 구분하기 위한 object_pairs_hook 결과다."""


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            pairs = json.load(file, object_pairs_hook=_JsonObjectPairs)
    except FileNotFoundError as error:
        raise TiltConfigurationError(f"{label} 파일이 없습니다: {path}") from error
    except OSError as error:
        raise TiltConfigurationError(f"{label} 파일을 읽지 못했습니다: {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise TiltConfigurationError(f"{label} JSON이 올바르지 않습니다: {path}") from error

    if not isinstance(pairs, _JsonObjectPairs):
        raise TiltConfigurationError(f"{label} 최상위 값은 JSON object여야 합니다.")

    result: dict[str, Any] = {}
    for pair in pairs:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise TiltConfigurationError(f"{label} JSON object 형식이 올바르지 않습니다.")
        key, value = pair
        if not isinstance(key, str):
            raise TiltConfigurationError(f"{label} key는 문자열이어야 합니다.")
        if key in result:
            raise TiltConfigurationError(f"{label}에 중복 key가 있습니다: {key}")
        result[key] = _convert_nested_pairs(value, label=label)
    return result


def _convert_nested_pairs(value: Any, *, label: str) -> Any:
    """``object_pairs_hook=list`` 결과를 중복 key 검증하며 일반 object로 바꾼다."""

    if isinstance(value, _JsonObjectPairs):
        if all(isinstance(item, tuple) and len(item) == 2 for item in value):
            result: dict[str, Any] = {}
            for key, nested in value:
                if not isinstance(key, str):
                    raise TiltConfigurationError(f"{label} key는 문자열이어야 합니다.")
                if key in result:
                    raise TiltConfigurationError(f"{label}에 중복 key가 있습니다: {key}")
                result[key] = _convert_nested_pairs(nested, label=label)
            return result
        raise TiltConfigurationError(f"{label} JSON object 형식이 올바르지 않습니다.")
    if isinstance(value, list):
        return [_convert_nested_pairs(item, label=label) for item in value]
    return value


def _strict_int_key(key: str, *, label: str) -> int:
    try:
        parsed = int(key)
    except ValueError as error:
        raise TiltConfigurationError(f"{label} key는 정수여야 합니다: {key!r}") from error
    if str(parsed) != key:
        raise TiltConfigurationError(f"{label} key 형식이 올바르지 않습니다: {key!r}")
    return parsed


def _finite_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TiltConfigurationError(f"{label}는 숫자여야 합니다.")
    result = float(value)
    if not math.isfinite(result):
        raise TiltConfigurationError(f"{label}는 유한한 숫자여야 합니다.")
    return result


class TiltLevelRepository:
    """레벨→mm과 duty/방향→평균 속도를 시작 시 한 번 읽어 불변으로 보관한다."""

    def __init__(self, levels_file: Path, calibration_file: Path) -> None:
        self._levels_file = levels_file
        self._calibration_file = calibration_file
        self._levels = self._load_levels(levels_file)
        self._calibration = self._load_calibration(calibration_file)

    def validate_for(self, *, min_level: int, max_level: int, move_duty_percent: int) -> None:
        """현재 장치 설정으로 안전하게 이동할 수 있는지 검증한다."""

        expected_levels = set(range(min_level, max_level + 1))
        actual_levels = set(self._levels)
        if actual_levels != expected_levels:
            missing = sorted(expected_levels - actual_levels)
            unexpected = sorted(actual_levels - expected_levels)
            details: list[str] = []
            if missing:
                details.append(f"누락 단계 {missing}")
            if unexpected:
                details.append(f"허용되지 않은 단계 {unexpected}")
            raise TiltConfigurationError("단계 목표 파일이 설정 범위와 다릅니다: " + ", ".join(details))

        previous: float | None = None
        for level in range(min_level, max_level + 1):
            target = self._levels[level]
            if target < 0:
                raise TiltConfigurationError(f"{level}단계 목표 위치는 0 이상이어야 합니다.")
            if previous is not None and target <= previous:
                raise TiltConfigurationError("단계 목표 위치는 엄격히 증가해야 합니다.")
            previous = target

        directions = self._calibration.get(move_duty_percent)
        if directions is None:
            raise TiltConfigurationError(
                f"duty {move_duty_percent}의 보정값이 없습니다: {self._calibration_file}"
            )
        for direction in DIRECTIONS:
            if direction not in directions:
                raise TiltConfigurationError(
                    f"duty {move_duty_percent}의 {direction} 보정값이 없습니다."
                )

    def target_mm_for_level(self, level: int) -> float | None:
        """지정 단계의 목표 위치(mm)를 반환한다."""

        return self._levels.get(level)

    def max_target_mm(self) -> float:
        """설정된 단계 중 가장 높은 목표 위치. 전체 행정 길이로 쓴다."""

        return max(self._levels.values(), default=0.0)

    def down_speed_mm_s(self, duty: int) -> float | None:
        """해당 duty의 하강 속도(mm/s). 보정이 없으면 None이다."""

        return self._calibration.get(duty, {}).get("DOWN")

    def calibration_snapshot(self) -> list[tuple[int, str, float]]:
        """ESP32에 전송할 (duty, 방향, 평균 mm/s) 목록을 반환한다."""

        return sorted(
            (duty, direction, speed)
            for duty, by_direction in self._calibration.items()
            for direction, speed in by_direction.items()
        )

    @staticmethod
    def _load_levels(path: Path) -> dict[int, float]:
        raw = _load_json_object(path, label="틸트 단계 목표")
        result: dict[int, float] = {}
        for key, value in raw.items():
            level = _strict_int_key(key, label="틸트 단계 목표")
            if level < 0:
                raise TiltConfigurationError("틸트 단계는 0 이상이어야 합니다.")
            result[level] = _finite_number(value, label=f"{level}단계 목표 위치")
        if not result:
            raise TiltConfigurationError("틸트 단계 목표 파일은 비어 있을 수 없습니다.")
        return result

    @staticmethod
    def _load_calibration(path: Path) -> dict[int, dict[str, float]]:
        raw = _load_json_object(path, label="틸트 속도 보정")
        result: dict[int, dict[str, float]] = {}
        for key, value in raw.items():
            duty = _strict_int_key(key, label="틸트 속도 보정")
            if not 1 <= duty <= 100:
                raise TiltConfigurationError("틸트 보정 duty는 1~100이어야 합니다.")
            if not isinstance(value, dict):
                raise TiltConfigurationError(f"duty {duty} 보정값은 JSON object여야 합니다.")
            if set(value) != set(DIRECTIONS):
                raise TiltConfigurationError(
                    f"duty {duty} 보정에는 UP과 DOWN만 각각 있어야 합니다."
                )
            directions: dict[str, float] = {}
            for direction in DIRECTIONS:
                samples = value[direction]
                if not isinstance(samples, list) or not samples:
                    raise TiltConfigurationError(f"duty {duty} {direction} 보정 표본이 없습니다.")
                speeds: list[float] = []
                for index, sample in enumerate(samples):
                    if not isinstance(sample, dict) or set(sample) - {"speed_mm_s", "at"}:
                        raise TiltConfigurationError(
                            f"duty {duty} {direction} 보정 표본 {index} 형식이 올바르지 않습니다."
                        )
                    if "speed_mm_s" not in sample:
                        raise TiltConfigurationError(
                            f"duty {duty} {direction} 보정 표본 {index}에 speed_mm_s가 없습니다."
                        )
                    speed = _finite_number(
                        sample["speed_mm_s"],
                        label=f"duty {duty} {direction} speed_mm_s",
                    )
                    if speed <= 0:
                        raise TiltConfigurationError(
                            f"duty {duty} {direction} speed_mm_s는 양수여야 합니다."
                        )
                    speeds.append(speed)
                directions[direction] = sum(speeds) / len(speeds)
            result[duty] = directions
        if not result:
            raise TiltConfigurationError("틸트 속도 보정 파일은 비어 있을 수 없습니다.")
        return result
