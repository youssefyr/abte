from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from app.core.vision.face_landmarker_wrapper import FaceLandmarkResult


@dataclass
class GazeFeatures:
    """Flat feature vector emitted each frame. Used by GazeMapper and ONNX regressor."""
    face_detected: float = 0.0
    face_count: float = 0.0
    # Iris
    right_iris_x: float = 0.5
    right_iris_y: float = 0.5
    left_iris_x: float = 0.5
    left_iris_y: float = 0.5
    iris_x_avg: float = 0.5
    iris_y_avg: float = 0.5
    iris_x_corrected: float = 0.5  # iris_x_avg minus neutral offset
    iris_y_corrected: float = 0.5
    # Head pose
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0
    yaw_corrected: float = 0.0
    pitch_corrected: float = 0.0
    # Eye openness
    eye_open_avg: float = 0.3
    right_ear: float = 0.3
    left_ear: float = 0.3
    # Mouth
    mouth_open_ratio: float = 0.0
    # Face quality
    face_center_score: float = 0.0
    face_size_score: float = 0.0
    iris_confidence: float = 0.0
    # Blinks (rolling)
    blink_rate_per_min: float = 0.0
    blink_count_30s: float = 0.0
    # Yawns
    yawn_count_5min: float = 0.0
    # Rolling window stats
    yaw_mean_5: float = 0.0
    yaw_std_5: float = 0.0
    pitch_mean_5: float = 0.0
    pitch_std_5: float = 0.0
    iris_x_mean_5: float = 0.0
    iris_x_std_5: float = 0.0
    eye_open_mean_5: float = 0.0
    no_face_ratio_5: float = 0.0
    # Frame quality
    is_low_light: float = 0.0
    is_blurry: float = 0.0
    laplacian_var: float = 0.0


class FeatureExtractor:
    BLINK_EAR_THRESHOLD = 0.25
    BLINK_MIN_FRAMES = 2
    YAWN_MOUTH_THRESHOLD = 0.45
    YAWN_MIN_FRAMES = 10

    def __init__(
        self,
        neutral_iris_x: float = 0.5,
        neutral_iris_y: float = 0.5,
        neutral_yaw: float = 0.0,
        neutral_pitch: float = 0.0,
        window_size: int = 10,
    ) -> None:
        self.neutral_iris_x = neutral_iris_x
        self.neutral_iris_y = neutral_iris_y
        self.neutral_yaw = neutral_yaw
        self.neutral_pitch = neutral_pitch
        self._window: deque[FaceLandmarkResult] = deque(maxlen=window_size)

        # Blink state machine
        self._blink_frames_below = 0
        self._in_blink = False
        self._blinks_30s: deque[float] = deque()
        self._blinks_60s: deque[float] = deque()

        # Yawn state machine
        self._yawn_frames_above = 0
        self._in_yawn = False
        self._yawns_5min: deque[float] = deque()

    def update_neutral(
        self,
        neutral_iris_x: float,
        neutral_iris_y: float,
        neutral_yaw: float,
        neutral_pitch: float,
    ) -> None:
        self.neutral_iris_x = neutral_iris_x
        self.neutral_iris_y = neutral_iris_y
        self.neutral_yaw = neutral_yaw
        self.neutral_pitch = neutral_pitch

    def extract(
        self,
        lm_result: FaceLandmarkResult,
        quality_meta: dict,
    ) -> GazeFeatures:
        now = time.time()
        self._window.append(lm_result)
        self._update_blinks(lm_result, now)
        self._update_yawns(lm_result, now)

        f = GazeFeatures()
        f.face_detected = float(lm_result.face_detected)
        f.face_count = float(lm_result.face_count)

        if lm_result.face_detected:
            ri_x = lm_result.right_iris_x or 0.5
            ri_y = lm_result.right_iris_y or 0.5
            li_x = lm_result.left_iris_x or 0.5
            li_y = lm_result.left_iris_y or 0.5

            f.right_iris_x = ri_x
            f.right_iris_y = ri_y
            f.left_iris_x = li_x
            f.left_iris_y = li_y
            f.iris_x_avg = (ri_x + li_x) / 2.0
            f.iris_y_avg = (ri_y + li_y) / 2.0
            f.iris_x_corrected = f.iris_x_avg - self.neutral_iris_x + 0.5
            f.iris_y_corrected = f.iris_y_avg - self.neutral_iris_y + 0.5

            f.yaw_deg = lm_result.yaw_deg or 0.0
            f.pitch_deg = lm_result.pitch_deg or 0.0
            f.roll_deg = lm_result.roll_deg or 0.0
            f.yaw_corrected = f.yaw_deg - self.neutral_yaw
            f.pitch_corrected = f.pitch_deg - self.neutral_pitch

            f.eye_open_avg = lm_result.eye_open_avg or 0.0
            f.right_ear = lm_result.right_ear or 0.0
            f.left_ear = lm_result.left_ear or 0.0
            f.mouth_open_ratio = lm_result.mouth_open_ratio or 0.0

            f.face_center_score = lm_result.face_center_score
            f.face_size_score = lm_result.face_size_score
            f.iris_confidence = lm_result.iris_confidence

        # Rolling window stats
        window_list = list(self._window)
        f.yaw_mean_5 = _safe_mean([r.yaw_deg for r in window_list if r.face_detected and r.yaw_deg is not None])
        f.yaw_std_5 = _safe_std([r.yaw_deg for r in window_list if r.face_detected and r.yaw_deg is not None])
        f.pitch_mean_5 = _safe_mean([r.pitch_deg for r in window_list if r.face_detected and r.pitch_deg is not None])
        f.pitch_std_5 = _safe_std([r.pitch_deg for r in window_list if r.face_detected and r.pitch_deg is not None])
        iris_xs = [
            ((r.right_iris_x or 0.5) + (r.left_iris_x or 0.5)) / 2.0
            for r in window_list if r.face_detected
        ]
        f.iris_x_mean_5 = _safe_mean(iris_xs)
        f.iris_x_std_5 = _safe_std(iris_xs)
        f.eye_open_mean_5 = _safe_mean([r.eye_open_avg for r in window_list if r.face_detected and r.eye_open_avg is not None])
        no_face_count = sum(1 for r in window_list if not r.face_detected)
        f.no_face_ratio_5 = no_face_count / max(len(window_list), 1)

        # Blink/yawn metrics
        f.blink_count_30s = float(len(self._blinks_30s))
        f.blink_rate_per_min = f.blink_count_30s * 2.0
        f.yawn_count_5min = float(len(self._yawns_5min))

        # Frame quality
        f.is_low_light = float(quality_meta.get("is_low_light", False))
        f.is_blurry = float(quality_meta.get("is_blurry", False))
        f.laplacian_var = float(quality_meta.get("laplacian_var", 0.0))

        return f

    def _update_blinks(self, lm: FaceLandmarkResult, now: float) -> None:
        # Clean old entries
        while self._blinks_30s and now - self._blinks_30s[0] > 30.0:
            self._blinks_30s.popleft()
        while self._blinks_60s and now - self._blinks_60s[0] > 60.0:
            self._blinks_60s.popleft()

        if not lm.face_detected or lm.eye_open_avg is None:
            self._blink_frames_below = 0
            self._in_blink = False
            return

        if lm.eye_open_avg < self.BLINK_EAR_THRESHOLD:
            self._blink_frames_below += 1
        else:
            if self._in_blink and self._blink_frames_below >= self.BLINK_MIN_FRAMES:
                self._blinks_30s.append(now)
                self._blinks_60s.append(now)
            self._blink_frames_below = 0
            self._in_blink = False

        if self._blink_frames_below >= self.BLINK_MIN_FRAMES:
            self._in_blink = True

    def _update_yawns(self, lm: FaceLandmarkResult, now: float) -> None:
        while self._yawns_5min and now - self._yawns_5min[0] > 300.0:
            self._yawns_5min.popleft()

        if not lm.face_detected or lm.mouth_open_ratio is None:
            self._yawn_frames_above = 0
            self._in_yawn = False
            return

        if lm.mouth_open_ratio > self.YAWN_MOUTH_THRESHOLD:
            self._yawn_frames_above += 1
        else:
            if self._in_yawn and self._yawn_frames_above >= self.YAWN_MIN_FRAMES:
                self._yawns_5min.append(now)
            self._yawn_frames_above = 0
            self._in_yawn = False

        if self._yawn_frames_above >= self.YAWN_MIN_FRAMES:
            self._in_yawn = True


def _safe_mean(values: list) -> float:
    clean = [v for v in values if v is not None]
    if not clean:
        return 0.0
    import numpy as np
    return float(np.mean(clean))


def _safe_std(values: list) -> float:
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return 0.0
    import numpy as np
    return float(np.std(clean))