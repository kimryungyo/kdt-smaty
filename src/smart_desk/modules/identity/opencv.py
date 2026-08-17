"""Private OpenCV SFace adapter; crops and landmarks never leave this module."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from smart_desk.modules.identity.service import FreshSingleFaceRequiredError
from smart_desk.modules.vision.models import FaceBox, FreshFaceObservation


class OpenCvSFaceEmbeddingExtractor:
    model_name = "opencv-sface"
    model_version = "2021dec"
    dimension = 128
    normalization = "l2"

    def __init__(self, model_path: Path, *, min_face_size: int, min_blur_variance: float,
                 min_brightness: float, max_brightness: float) -> None:
        if not model_path.is_file():
            raise FileNotFoundError(f"SFace model not found: {model_path}")
        self._recognizer = cv2.FaceRecognizerSF.create(str(model_path), "")
        if self._recognizer is None:
            raise RuntimeError("Unable to create OpenCV SFace recognizer")
        self._min_face_size = min_face_size
        self._min_blur_variance = min_blur_variance
        self._min_brightness = min_brightness
        self._max_brightness = max_brightness

    def extract(self, observation: FreshFaceObservation) -> tuple[float, ...] | None:
        if len(observation.boxes) != 1:
            raise FreshSingleFaceRequiredError("FRESH_SINGLE_FACE_REQUIRED")
        box = observation.boxes[0]
        if not self._valid_box(box, observation.frame):
            return None
        crop = observation.frame[box.y:box.y + box.height, box.x:box.x + box.width]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        brightness, blur = float(gray.mean()), float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if (not np.isfinite((brightness, blur)).all() or blur < self._min_blur_variance
                or not self._min_brightness <= brightness <= self._max_brightness):
            return None
        row = np.asarray([[box.x, box.y, box.width, box.height,
                           *(coordinate for point in box.landmarks for coordinate in point),
                           box.confidence]], dtype=np.float32)
        aligned = self._recognizer.alignCrop(observation.frame, row)
        feature = np.asarray(self._recognizer.feature(aligned), dtype=np.float64).reshape(-1)
        if feature.shape != (self.dimension,) or not np.isfinite(feature).all():
            return None
        norm = float(np.linalg.norm(feature))
        if not np.isfinite(norm) or norm <= 0:
            return None
        return tuple(float(value) for value in feature / norm)

    def _valid_box(self, box: FaceBox, frame: np.ndarray) -> bool:
        if frame.ndim != 3 or frame.shape[0] <= 0 or frame.shape[1] <= 0:
            return False
        if box.width < self._min_face_size or box.height < self._min_face_size:
            return False
        if len(box.landmarks) != 5 or box.confidence is None:
            return False
        values = [box.x, box.y, box.width, box.height, box.confidence,
                  *(coordinate for point in box.landmarks for coordinate in point)]
        if (
            not np.isfinite(values).all()
            or box.x < 0
            or box.y < 0
            or not 0 <= box.confidence <= 1
            or any(
                point_x < 0
                or point_y < 0
                or point_x >= frame.shape[1]
                or point_y >= frame.shape[0]
                for point_x, point_y in box.landmarks
            )
        ):
            return False
        return box.x + box.width <= frame.shape[1] and box.y + box.height <= frame.shape[0]
