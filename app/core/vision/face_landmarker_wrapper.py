from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python.vision import FaceLandmarkerResult

logger = logging.getLogger(__name__)

# MediaPipe iris landmark indices inside FaceLandmarker 478-point mesh
# Right iris: 468-472, Left iris: 473-477
_RIGHT_IRIS = [468, 469, 470, 471, 472]
_LEFT_IRIS = [473, 474, 475, 476, 477]
# EAR landmark indices from MediaPipe 478-point canonical face mesh
# Right eye: [33,160,158,133,153,144], Left eye: [362,385,387,263,373,380]
_RIGHT_EYE_EAR = [33, 160, 158, 133, 153, 144]
_LEFT_EYE_EAR = [362, 385, 387, 263, 373, 380]


@dataclass
class FaceLandmarkResult:
    face_detected: bool = False
    face_count: int = 0
    # Iris centres (normalised 0-1 in frame)
    right_iris_x: Optional[float] = None
    right_iris_y: Optional[float] = None
    left_iris_x: Optional[float] = None
    left_iris_y: Optional[float] = None
    # Head pose in degrees
    yaw_deg: Optional[float] = None
    pitch_deg: Optional[float] = None
    roll_deg: Optional[float] = None
    # Eye openness
    right_ear: Optional[float] = None
    left_ear: Optional[float] = None
    eye_open_avg: Optional[float] = None
    # Mouth open ratio (for yawn detection)
    mouth_open_ratio: Optional[float] = None
    # Face bounding box quality metrics
    face_center_score: float = 0.0   # 0-1, how centred in frame
    face_size_score: float = 0.0     # 0-1, face occupies useful area
    # Blendshape scores
    blink_left_score: float = 0.0
    blink_right_score: float = 0.0
    # Iris confidence (proxy: face size score * not blurry)
    iris_confidence: float = 0.0


class FaceLandmarkerWrapper:
    """
    Thin wrapper around MediaPipe FaceLandmarker Task.
    Provides a synchronous process_frame() interface,
    returning FaceLandmarkResult every call.
    """

    # Blendshape names from MediaPipe canonical list
    _BLINK_LEFT_IDX = 9    # eyeBlinkLeft
    _BLINK_RIGHT_IDX = 10  # eyeBlinkRight

    def __init__(self, model_path: str | Path) -> None:
        self._model_path = Path(model_path)
        self._landmarker: Optional[Any] = None
        self._load_model()

    def _load_model(self) -> None:
        if not self._model_path.exists():
            raise FileNotFoundError(f"FaceLandmarker model not found: {self._model_path}")

        base_opts = mp_python.BaseOptions(
            model_asset_path=str(self._model_path),
            delegate=mp_python.BaseOptions.Delegate.CPU
        )
        opts = mp_vision.FaceLandmarkerOptions(
            base_options=base_opts,
            running_mode=mp_vision.RunningMode.IMAGE,
            num_faces=1,
            min_face_detection_confidence=0.45,
            min_face_presence_confidence=0.45,
            min_tracking_confidence=0.45,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
        try:
            self._landmarker = mp_vision.FaceLandmarker.create_from_options(opts)
        except Exception as exc:
            logger.error(f"Failed to load FaceLandmarker: {exc}")
            self._landmarker = None

    def process_frame(self, bgr_frame: np.ndarray) -> FaceLandmarkResult:
        result = FaceLandmarkResult()
        if self._landmarker is None:
            return result

        h, w = bgr_frame.shape[:2]
        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        try:
            detection = self._landmarker.detect(mp_image)
        except Exception as exc:
            logger.debug(f"FaceLandmarker detect error: {exc}")
            return result

        if not detection.face_landmarks:
            return result

        result.face_detected = True
        result.face_count = len(detection.face_landmarks)
        lm = detection.face_landmarks[0]

        # --- Iris centres (normalised) ---
        def iris_centre(indices: list[int]) -> tuple[float, float]:
            pts = [(lm[i].x, lm[i].y) for i in indices if i < len(lm)]
            if not pts:
                return 0.5, 0.5
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            return float(np.mean(xs)), float(np.mean(ys))

        result.right_iris_x, result.right_iris_y = iris_centre(_RIGHT_IRIS)
        result.left_iris_x, result.left_iris_y = iris_centre(_LEFT_IRIS)

        # --- EAR ---
        def ear(indices: list[int]) -> float:
            pts = np.array([[lm[i].x * w, lm[i].y * h] for i in indices])
            A = float(np.linalg.norm(pts[1] - pts[5]))
            B = float(np.linalg.norm(pts[2] - pts[4]))
            C = float(np.linalg.norm(pts[0] - pts[3]))
            if C < 1e-6:
                return 0.0
            return (A + B) / (2.0 * C)

        result.right_ear = ear(_RIGHT_EYE_EAR)
        result.left_ear = ear(_LEFT_EYE_EAR)
        result.eye_open_avg = (result.right_ear + result.left_ear) / 2.0

        # --- Mouth open ratio (top lip centre to bottom lip centre) ---
        # Canonical: 13 = upper inner lip, 14 = lower inner lip, 78 = right mouth, 308 = left mouth
        try:
            upper = np.array([lm[13].x * w, lm[13].y * h])
            lower = np.array([lm[14].x * w, lm[14].y * h])
            left_m = np.array([lm[78].x * w, lm[78].y * h])
            right_m = np.array([lm[308].x * w, lm[308].y * h])
            v_dist = float(np.linalg.norm(upper - lower))
            h_dist = float(np.linalg.norm(left_m - right_m))
            result.mouth_open_ratio = v_dist / (h_dist + 1e-6)
        except Exception:
            result.mouth_open_ratio = 0.0

        # --- Head pose from transformation matrix ---
        if detection.facial_transformation_matrixes:
            mat = np.array(detection.facial_transformation_matrixes[0])
            yaw, pitch, roll = self._rotation_matrix_to_euler(mat[:3, :3])
            result.yaw_deg = float(np.degrees(yaw))
            result.pitch_deg = float(np.degrees(pitch))
            result.roll_deg = float(np.degrees(roll))

        # --- Blendshapes ---
        if detection.face_blendshapes:
            bs = detection.face_blendshapes[0]
            try:
                result.blink_left_score = float(bs[self._BLINK_LEFT_IDX].score)
                result.blink_right_score = float(bs[self._BLINK_RIGHT_IDX].score)
            except (IndexError, AttributeError):
                pass

        # --- Face quality metrics ---
        xs = [l.x for l in lm]
        ys = [l.y for l in lm]
        cx = float(np.mean(xs))
        cy = float(np.mean(ys))
        result.face_center_score = float(1.0 - 2.0 * np.sqrt((cx - 0.5) ** 2 + (cy - 0.5) ** 2))
        result.face_center_score = max(0.0, result.face_center_score)

        x_span = float(max(xs) - min(xs))
        y_span = float(max(ys) - min(ys))
        face_area = x_span * y_span
        result.face_size_score = float(np.clip(face_area / 0.25, 0.0, 1.0))

        result.iris_confidence = float(result.face_size_score * result.face_center_score)

        return result

    @staticmethod
    def _rotation_matrix_to_euler(R: np.ndarray) -> tuple[float, float, float]:
        # ZYX Euler decomposition
        sy = float(np.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2))
        singular = sy < 1e-6
        if not singular:
            x = float(np.arctan2(R[2, 1], R[2, 2]))
            y = float(np.arctan2(-R[2, 0], sy))
            z = float(np.arctan2(R[1, 0], R[0, 0]))
        else:
            x = float(np.arctan2(-R[1, 2], R[1, 1]))
            y = float(np.arctan2(-R[2, 0], sy))
            z = 0.0
        return x, y, z

    def close(self) -> None:
        if self._landmarker is not None:
            try:
                self._landmarker.close()
            except Exception:
                pass
            self._landmarker = None