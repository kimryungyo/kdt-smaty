"""틸팅 단계(mm) 목표와 duty→speed 보정 데이터를 JSON 파일에서 읽는다.

ESP32 펌웨어(`tilt-hw039`)의 보정 테이블은 RAM에만 있어 재부팅 시 사라진다.
이 저장소는 연결/재연결마다 재전송할 값을 프로세스 시작 시 한 번 읽어
보관하는 읽기 전용 소스다. `.scratch/tilt_project/tilt_controller.py`에서
실측한 값을 그대로 옮긴 `data/tilt_levels.json`,
`data/tilt_calibration.json`을 읽는다.
"""

from __future__ import annotations

import json
from pathlib import Path


DIRECTIONS = ("UP", "DOWN")


def _load_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _average_speed(samples: list[dict]) -> float:
    speeds = [sample["speed_mm_s"] for sample in samples]
    return sum(speeds) / len(speeds)


class TiltLevelRepository:
    """레벨→mm과 duty/방향→평균속도(mm/s)를 프로세스 시작 시 한 번 읽어 보관한다."""

    def __init__(self, levels_file: Path, calibration_file: Path) -> None:
        self._levels = self._load_levels(levels_file)
        self._calibration = self._load_calibration(calibration_file)

    def target_mm_for_level(self, level: int) -> float | None:
        """지정 단계의 목표 위치(mm)를 반환한다. 보정되지 않은 단계는 None."""

        return self._levels.get(level)

    def calibration_snapshot(self) -> list[tuple[int, str, float]]:
        """재연결마다 ESP32에 재전송할 (duty, 방향, 평균 mm/s) 목록을 반환한다."""

        return sorted(
            (duty, direction, _average_speed(samples))
            for duty, by_direction in self._calibration.items()
            for direction, samples in by_direction.items()
        )

    @staticmethod
    def _load_levels(path: Path) -> dict[int, float]:
        raw = _load_json(path)
        result: dict[int, float] = {}
        for key, value in raw.items():
            if value is None:
                continue
            try:
                result[int(key)] = float(value)
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def _load_calibration(path: Path) -> dict[int, dict[str, list[dict]]]:
        raw = _load_json(path)
        result: dict[int, dict[str, list[dict]]] = {}
        for key, value in raw.items():
            try:
                duty = int(key)
            except (TypeError, ValueError):
                continue
            if not isinstance(value, dict):
                continue
            by_direction: dict[str, list[dict]] = {}
            for direction in DIRECTIONS:
                samples = [
                    sample
                    for sample in value.get(direction, [])
                    if isinstance(sample, dict) and "speed_mm_s" in sample
                ]
                if samples:
                    by_direction[direction] = samples
            if by_direction:
                result[duty] = by_direction
        return result
