from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np

from app.core.vision.face_landmarker_wrapper import FaceLandmarkerWrapper, FaceLandmarkResult
from app.core.vision.frame_enhancer import FrameEnhancer
from app.core.vision.gaze_mapper import PolynomialGazeMapper
from app.calibration.calibration_store import CalibrationStore

logger = logging.getLogger(__name__)


class NaturalGazeCollector:
    """
    Collects face data while user looks at screen centre.
    Used in Phase 0 of calibration.
    """

    def __init__(self, duration_seconds: float = 4.0) -> None:
        self._duration = duration_seconds
        self._iris_xs: list[float] = []
        self._iris_ys: list[float] = []
        self._yaws: list[float] = []
        self._pitches: list[float] = []
        self._start_time: Optional[float] = None
        self._finished = False

    def reset(self) -> None:
        self._iris_xs.clear()
        self._iris_ys.clear()
        self._yaws.clear()
        self._pitches.clear()
        self._start_time = None
        self._finished = False

    def start(self) -> None:
        self._start_time = time.time()

    def feed(self, lm: FaceLandmarkResult) -> None:
        if self._finished or self._start_time is None:
            return
        if lm.face_detected and lm.right_iris_x is not None:
            iris_x = ((lm.right_iris_x or 0.5) + (lm.left_iris_x or 0.5)) / 2.0
            iris_y = ((lm.right_iris_y or 0.5) + (lm.left_iris_y or 0.5)) / 2.0
            self._iris_xs.append(iris_x)
            self._iris_ys.append(iris_y)
            if lm.yaw_deg is not None:
                self._yaws.append(lm.yaw_deg)
            if lm.pitch_deg is not None:
                self._pitches.append(lm.pitch_deg)
        if time.time() - self._start_time >= self._duration:
            self._finished = True

    @property
    def is_finished(self) -> bool:
        return self._finished

    @property
    def progress(self) -> float:
        if self._start_time is None:
            return 0.0
        return min(1.0, (time.time() - self._start_time) / self._duration)

    def result(self) -> dict:
        if not self._iris_xs:
            return {"neutral_iris_x": 0.5, "neutral_iris_y": 0.5, "neutral_yaw": 0.0, "neutral_pitch": 0.0, "samples": 0}
        return {
            "neutral_iris_x": float(np.median(self._iris_xs)),
            "neutral_iris_y": float(np.median(self._iris_ys)),
            "neutral_yaw": float(np.median(self._yaws)) if self._yaws else 0.0,
            "neutral_pitch": float(np.median(self._pitches)) if self._pitches else 0.0,
            "samples": len(self._iris_xs),
        }


class PointCalibrationCollector:
    """
    Collects iris data for one calibration target point.
    Discards first half second as stabilisation frames.
    """

    STABILISE_SECONDS = 0.5
    COLLECT_SECONDS = 1.0

    def __init__(self) -> None:
        self._start_time: Optional[float] = None
        self._iris_xs: list[float] = []
        self._iris_ys: list[float] = []
        self._yaws: list[float] = []
        self._pitches: list[float] = []
        self._finished = False

    def reset(self) -> None:
        self._start_time = None
        self._iris_xs.clear()
        self._iris_ys.clear()
        self._yaws.clear()
        self._pitches.clear()
        self._finished = False

    def start(self) -> None:
        self._start_time = time.time()

    def feed(self, lm: FaceLandmarkResult) -> None:
        if self._finished or self._start_time is None:
            return
        elapsed = time.time() - self._start_time
        if elapsed < self.STABILISE_SECONDS:
            return
        total = self.STABILISE_SECONDS + self.COLLECT_SECONDS
        if elapsed > total:
            self._finished = True
            return
        if lm.face_detected and lm.right_iris_x is not None:
            self._iris_xs.append(((lm.right_iris_x or 0.5) + (lm.left_iris_x or 0.5)) / 2.0)
            self._iris_ys.append(((lm.right_iris_y or 0.5) + (lm.left_iris_y or 0.5)) / 2.0)
            if lm.yaw_deg is not None:
                self._yaws.append(lm.yaw_deg)
            if lm.pitch_deg is not None:
                self._pitches.append(lm.pitch_deg)

    @property
    def is_finished(self) -> bool:
        return self._finished

    @property
    def progress(self) -> float:
        if self._start_time is None:
            return 0.0
        total = self.STABILISE_SECONDS + self.COLLECT_SECONDS
        return min(1.0, (time.time() - self._start_time) / total)

    def get_median(self) -> Optional[tuple[float, float, float, float]]:
        if not self._iris_xs:
            return None
        return (
            float(np.median(self._iris_xs)),
            float(np.median(self._iris_ys)),
            float(np.median(self._yaws)) if self._yaws else 0.0,
            float(np.median(self._pitches)) if self._pitches else 0.0,
        )


def compute_calibration_targets(screen_available_rect: tuple[int, int, int, int]) -> list[tuple[float, float]]:
    """
    Returns 9 normalised (0-1) screen coordinate targets for the calibration
    grid adjusted to available geometry (excludes taskbar/dock).
    Inset: 5% from each available edge to avoid panel collision.
    screen_available_rect: (x, y, width, height) from QScreen.availableGeometry()
    """
    ax, ay, aw, ah = screen_available_rect
    inset_x = 0.05
    inset_y = 0.05

    points_norm = [
        (inset_x, inset_y),            # top-left
        (0.5, inset_y),                # top-center
        (1.0 - inset_x, inset_y),      # top-right
        (inset_x, 0.5),                # mid-left
        (0.5, 0.5),                    # centre
        (1.0 - inset_x, 0.5),          # mid-right
        (inset_x, 1.0 - inset_y),      # bottom-left
        (0.5, 1.0 - inset_y),          # bottom-center
        (1.0 - inset_x, 1.0 - inset_y), # bottom-right
    ]
    return points_norm