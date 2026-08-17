"""실제 모델 전의 최소 detector adapter 경계."""

from __future__ import annotations

from typing import Protocol

import numpy as np

from smart_desk.modules.vision.models import LowerDetection, UpperDetection


class VisionDetector(Protocol):
    """CPU-bound 호출이며 VisionService가 executor에서 실행한다."""

    def detect_upper(self, frame: np.ndarray) -> UpperDetection: ...

    def detect_lower(self, frame: np.ndarray) -> LowerDetection: ...


class NoopVisionDetector:
    """실물 model/ROI가 확정되기 전 fail-closed 기본 adapter다."""

    def detect_upper(self, _frame: np.ndarray) -> UpperDetection:
        return UpperDetection(body_count=None)

    def detect_lower(self, _frame: np.ndarray) -> LowerDetection:
        return LowerDetection(count=None)
