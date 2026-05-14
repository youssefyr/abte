from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal

from app.core.vision.face_landmarker_wrapper import FaceLandmarkerWrapper
from app.core.vision.feature_extractor import FeatureExtractor, GazeFeatures
from app.core.vision.frame_enhancer import FrameEnhancer
from app.core.vision.gaze_mapper import PolynomialGazeMapper, GazePoint
from app.core.vision.gaze_zone_classifier import GazeZoneClassifier, GazeResult

logger = logging.getLogger(__name__)


class GazeWorker(QThread):
    """
    Runs the full gaze pipeline at ~10 FPS in a QThread.
    Emits gaze_result signal with GazeResult each cycle.
    Emits frame_quality signal with quality metadata for diagnostics.
    """

    gaze_result = Signal(object)    # GazeResult
    frame_quality = Signal(dict)    # quality meta
    camera_status = Signal(str, str)  # status, detail

    TARGET_FPS = 10
    FRAME_INTERVAL_MS = int(1000 / TARGET_FPS)

    def __init__(
        self,
        model_path: str | Path,
        camera_index: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._model_path = Path(model_path)
        self._camera_index = camera_index
        self._running = False

        # Components — created in run() to be thread-safe
        self._enhancer: Optional[FrameEnhancer] = None
        self._landmarker: Optional[FaceLandmarkerWrapper] = None
        self._extractor: Optional[FeatureExtractor] = None
        self._mapper: Optional[PolynomialGazeMapper] = None
        self._classifier: Optional[GazeZoneClassifier] = None

        # Screen geometry for coordinate mapping
        self._active_screen_id: str = ""
        self._active_screen_w: int = 1920
        self._active_screen_h: int = 1080

        # Shared state updated from main thread via methods
        self._mapper_ref: Optional[PolynomialGazeMapper] = None
        self._neutral_iris_x: float = 0.5
        self._neutral_iris_y: float = 0.5
        self._neutral_yaw: float = 0.0
        self._neutral_pitch: float = 0.0

    def set_mapper(self, mapper: PolynomialGazeMapper) -> None:
        """Called from the main thread after calibration."""
        self._mapper_ref = mapper

    def set_neutral(self, iris_x: float, iris_y: float, yaw: float, pitch: float) -> None:
        self._neutral_iris_x = iris_x
        self._neutral_iris_y = iris_y
        self._neutral_yaw = yaw
        self._neutral_pitch = pitch

    def set_active_screen(self, screen_id: str, w: int, h: int) -> None:
        self._active_screen_id = screen_id
        self._active_screen_w = w
        self._active_screen_h = h

    def run(self) -> None:
        self._running = True

        self._enhancer = FrameEnhancer()
        self._extractor = FeatureExtractor(
            neutral_iris_x=self._neutral_iris_x,
            neutral_iris_y=self._neutral_iris_y,
            neutral_yaw=self._neutral_yaw,
            neutral_pitch=self._neutral_pitch,
        )
        self._classifier = GazeZoneClassifier(is_calibrated=False)

        try:
            self._landmarker = FaceLandmarkerWrapper(self._model_path)
        except FileNotFoundError as exc:
            logger.error(f"GazeWorker: model not found — {exc}. Running without vision.")
            self._running = False
            return

        cap = self._open_camera(self._camera_index)
        if not cap.isOpened():
            logger.warning("GazeWorker: camera not available, stopping.")
            self.camera_status.emit("unavailable", "camera_open_failed")
            self._running = False
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 30)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        last_frame_time = 0.0

        consecutive_failures = 0
        while self._running and not self.isInterruptionRequested():
            now = time.time()
            elapsed_ms = (now - last_frame_time) * 1000
            if elapsed_ms < self.FRAME_INTERVAL_MS:
                sleep_ms = int(self.FRAME_INTERVAL_MS - elapsed_ms)
                self.msleep(max(sleep_ms, 1))
                continue

            last_frame_time = time.time()

            ret, frame = cap.read()
            if not ret:
                logger.debug("GazeWorker: empty frame")
                consecutive_failures += 1
                if consecutive_failures >= 20:
                    self.camera_status.emit("lost", "camera_read_failed")
                    break
                continue
            consecutive_failures = 0

            # 1. Enhance
            enhanced, quality_meta = self._enhancer.enhance(frame)

            # 2. Detect landmarks
            lm_result = self._landmarker.process_frame(enhanced)

            # 3. Extract features
            if self._extractor.neutral_iris_x != self._neutral_iris_x:
                self._extractor.update_neutral(
                    self._neutral_iris_x,
                    self._neutral_iris_y,
                    self._neutral_yaw,
                    self._neutral_pitch,
                )
            features = self._extractor.extract(lm_result, quality_meta)

            # 4. Map gaze
            gaze_point: Optional[GazePoint] = None
            mapper = self._mapper_ref
            if mapper is not None and mapper.is_calibrated(self._active_screen_id) and lm_result.face_detected:
                gaze_point = mapper.predict(
                    screen_id=self._active_screen_id,
                    iris_x=features.iris_x_avg,
                    iris_y=features.iris_y_avg,
                    yaw=features.yaw_corrected,
                    pitch=features.pitch_corrected,
                    screen_w=self._active_screen_w,
                    screen_h=self._active_screen_h,
                    iris_confidence=features.iris_confidence,
                )
                self._classifier.set_calibrated(True)
            else:
                self._classifier.set_calibrated(mapper is not None and mapper.is_calibrated(self._active_screen_id))

            # 5. Classify zone
            result = self._classifier.classify(gaze_point, features)

            self.gaze_result.emit(result)
            self.frame_quality.emit(quality_meta)

        cap.release()
        if self._landmarker:
            self._landmarker.close()

    def stop_request(self) -> None:
        """Signal the run loop to exit. Does NOT block — caller owns the wait()."""
        self._running = False
        self.requestInterruption()

    @staticmethod
    def _open_camera(camera_index: int) -> cv2.VideoCapture:
        index = int(camera_index) if isinstance(camera_index, int) else 0
        index = max(0, index)
        if sys.platform.startswith("linux"):
            return cv2.VideoCapture(index, cv2.CAP_V4L2)
        if sys.platform == "darwin":
            return cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
        if sys.platform.startswith("win"):
            return cv2.VideoCapture(index, cv2.CAP_DSHOW)
        return cv2.VideoCapture(index)