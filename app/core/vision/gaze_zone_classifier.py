from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.core.vision.gaze_mapper import GazePoint
from app.core.vision.feature_extractor import GazeFeatures


class GazeZone(str, Enum):
    ON_SCREEN = "on_screen"
    OFF_SCREEN = "off_screen"      # gaze point outside screen bounds
    LOOKING_AWAY = "looking_away"  # head pose beyond safe range
    ABSENT = "absent"              # no face detected
    DEGRADED = "degraded"          # face detected but low confidence
    NOT_CALIBRATED = "not_calibrated"


@dataclass
class GazeResult:
    zone: GazeZone = GazeZone.ABSENT
    gaze_x_norm: float = 0.5
    gaze_y_norm: float = 0.5
    gaze_x_px: int = 0
    gaze_y_px: int = 0
    screen_id: str = ""
    confidence: float = 0.0
    face_detected: bool = False
    blink_rate_per_min: float = 0.0
    yawn_count_5min: float = 0.0
    eye_open_avg: float = 0.0
    is_blinking: bool = False
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    is_low_light: bool = False
    is_blurry: bool = False
    calibration_noise: float = 0.0


class GazeZoneClassifier:
    """
    Combines GazePoint + GazeFeatures → GazeResult with zone label.
    """

    HEAD_POSE_YAW_LIMIT = 45.0
    HEAD_POSE_PITCH_LIMIT = 35.0
    MIN_FACE_SIZE_FOR_GAZE = 0.05
    BLINK_EAR_THRESHOLD = 0.25

    def __init__(self, is_calibrated: bool = False) -> None:
        self._is_calibrated = is_calibrated

    def set_calibrated(self, value: bool) -> None:
        self._is_calibrated = value

    def classify(
        self,
        gaze_point: GazePoint | None,
        features: GazeFeatures,
    ) -> GazeResult:
        result = GazeResult()
        result.face_detected = bool(features.face_detected)
        result.blink_rate_per_min = features.blink_rate_per_min
        result.yawn_count_5min = features.yawn_count_5min
        result.eye_open_avg = features.eye_open_avg
        result.is_blinking = features.eye_open_avg < self.BLINK_EAR_THRESHOLD
        result.yaw_deg = features.yaw_deg
        result.pitch_deg = features.pitch_deg
        result.is_low_light = bool(features.is_low_light)
        result.is_blurry = bool(features.is_blurry)

        if not self._is_calibrated:
            result.zone = GazeZone.NOT_CALIBRATED
            return result

        if not features.face_detected:
            result.zone = GazeZone.ABSENT
            return result

        if features.face_size_score < self.MIN_FACE_SIZE_FOR_GAZE:
            result.zone = GazeZone.DEGRADED
            result.confidence = features.iris_confidence
            return result

        # Head-pose looking away (even with face detected)
        yaw_abs = abs(features.yaw_corrected)
        pitch_abs = abs(features.pitch_corrected)
        if yaw_abs > self.HEAD_POSE_YAW_LIMIT or pitch_abs > self.HEAD_POSE_PITCH_LIMIT:
            result.zone = GazeZone.LOOKING_AWAY
            result.confidence = features.iris_confidence
            return result

        if gaze_point is None:
            result.zone = GazeZone.DEGRADED
            return result

        result.gaze_x_norm = gaze_point.x_norm
        result.gaze_y_norm = gaze_point.y_norm
        result.gaze_x_px = gaze_point.x_px
        result.gaze_y_px = gaze_point.y_px
        result.screen_id = gaze_point.screen_id
        result.confidence = gaze_point.confidence
        result.calibration_noise = getattr(gaze_point, 'calibration_noise', 0.0)

        # Check if gaze point is within screen bounds (with small tolerance margin)
        margin = 0.05
        if (
            -margin <= gaze_point.x_norm <= 1.0 + margin
            and -margin <= gaze_point.y_norm <= 1.0 + margin
        ):
            result.zone = GazeZone.ON_SCREEN
        else:
            result.zone = GazeZone.OFF_SCREEN

        return result