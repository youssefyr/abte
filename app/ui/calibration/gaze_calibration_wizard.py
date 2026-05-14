from __future__ import annotations

import logging
import sys
import time
from typing import Optional

import cv2
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QScreen
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from app.calibration.calibration_engine import (
    NaturalGazeCollector,
    PointCalibrationCollector,
    compute_calibration_targets,
)
from app.core.vision.face_landmarker_wrapper import FaceLandmarkerWrapper
from app.core.vision.frame_enhancer import FrameEnhancer

logger = logging.getLogger(__name__)


class CalibrationDotWidget(QWidget):
    """Full-screen transparent overlay showing a shrinking animated dot."""

    def __init__(self, screen: QScreen, parent=None) -> None:
        super().__init__(parent)
        self.screen_ref = screen
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self.setGeometry(screen.geometry())
        self._target_x_norm: float = 0.5
        self._target_y_norm: float = 0.5
        self._dot_radius: float = 30.0
        self._progress: float = 0.0
        self._phase_label: str = ""

    def set_target(self, x_norm: float, y_norm: float) -> None:
        self._target_x_norm = x_norm
        self._target_y_norm = y_norm
        self._dot_radius = 30.0
        self.update()

    def set_progress(self, progress: float, phase_label: str = "") -> None:
        self._progress = progress
        self._dot_radius = max(6.0, 30.0 * (1.0 - progress))
        self._phase_label = phase_label
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))

        w = self.width()
        h = self.height()
        cx = int(self._target_x_norm * w)
        cy = int(self._target_y_norm * h)
        r = int(self._dot_radius)

        # Outer ring
        painter.setPen(QPen(QColor(255, 255, 255, 200), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(cx - 30, cy - 30, 60, 60)

        # Filling dot (progress indicator)
        fill_color = QColor(100, 220, 255, 220)
        painter.setBrush(fill_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        # Phase label
        if self._phase_label:
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(w // 2 - 200, h - 80, 400, 40, Qt.AlignmentFlag.AlignCenter, self._phase_label)

        painter.end()


class GazeCalibrationWizard(QWidget):
    """
    Multi-phase gaze calibration wizard.
    Phase 0: natural gaze baseline (4 s at centre).
    Phase 1: 9-point grid collection per screen.
    Phase 2: fit and emit result.
    """

    calibration_finished = Signal(dict)   # emits calibration_data dict
    calibration_cancelled = Signal()

    def __init__(
        self,
        screen: QScreen,
        model_path: str,
        camera_index: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self._screen = screen
        self._model_path = model_path
        self._camera_index = camera_index

        ag = screen.availableGeometry()
        self._available_rect = (ag.x(), ag.y(), ag.width(), ag.height())

        self._screen_id = self._make_screen_id(screen)
        self._targets = compute_calibration_targets(self._available_rect)

        self._enhancer = FrameEnhancer()
        self._landmarker: Optional[FaceLandmarkerWrapper] = None
        self._cap: Optional[cv2.VideoCapture] = None

        self._natural_collector = NaturalGazeCollector(duration_seconds=4.0)
        self._point_collector = PointCalibrationCollector()
        self._current_target_idx = 0

        # Collected calibration data
        self._calib_iris_xs: list[float] = []
        self._calib_iris_ys: list[float] = []
        self._calib_yaws: list[float] = []
        self._calib_pitches: list[float] = []
        self._calib_screen_xs: list[float] = []
        self._calib_screen_ys: list[float] = []

        self._phase = "phase0"  # phase0 | phase1 | done
        self._natural_gaze_result: dict = {}

        self._dot_widget = CalibrationDotWidget(screen, parent=None)
        self._dot_widget.set_target(0.5, 0.5)
        self._dot_widget.set_progress(0.0, "Phase 1/2: Look at the centre dot")
        self._dot_widget.show()
        handle = self._dot_widget.windowHandle()
        if handle is not None:
            handle.setScreen(screen)
        self._dot_widget.setGeometry(screen.geometry())
        self._dot_widget.raise_()

        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)

        self._init_camera()
        self._init_model()
        self._natural_collector.start()
        self._timer.start()

    def _make_screen_id(self, screen: QScreen) -> str:
        g = screen.geometry()
        return f"{screen.name()}_{g.width()}x{g.height()}"

    def _init_camera(self) -> None:
        self._cap = self._open_camera(self._camera_index)
        if self._cap.isOpened():
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def _init_model(self) -> None:
        try:
            self._landmarker = FaceLandmarkerWrapper(self._model_path)
        except FileNotFoundError:
            logger.error("Calibration wizard: FaceLandmarker model not found.")
            self._landmarker = None

    def _read_landmarks(self):
        if self._cap is None or not self._cap.isOpened() or self._landmarker is None:
            return None
        ret, frame = self._cap.read()
        if not ret:
            return None
        enhanced, _ = self._enhancer.enhance(frame)
        return self._landmarker.process_frame(enhanced)

    def _tick(self) -> None:
        lm = self._read_landmarks()

        if self._phase == "phase0":
            if lm:
                self._natural_collector.feed(lm)
            progress = self._natural_collector.progress
            self._dot_widget.set_progress(progress, "Phase 1/2: Look naturally at the centre dot")
            if self._natural_collector.is_finished:
                self._natural_gaze_result = self._natural_collector.result()
                logger.info(f"Natural gaze collected: {self._natural_gaze_result}")
                self._phase = "phase1"
                self._current_target_idx = 0
                self._start_next_point()

        elif self._phase == "phase1":
            if lm:
                self._point_collector.feed(lm)
            progress = self._point_collector.progress
            tx, ty = self._targets[self._current_target_idx]
            label = f"Phase 2/2: Point {self._current_target_idx + 1} of {len(self._targets)}"
            self._dot_widget.set_target(tx, ty)
            self._dot_widget.set_progress(progress, label)

            if self._point_collector.is_finished:
                median = self._point_collector.get_median()
                if median is not None:
                    iris_x, iris_y, yaw, pitch = median
                    self._calib_iris_xs.append(iris_x)
                    self._calib_iris_ys.append(iris_y)
                    self._calib_yaws.append(yaw)
                    self._calib_pitches.append(pitch)
                    self._calib_screen_xs.append(tx)
                    self._calib_screen_ys.append(ty)

                self._current_target_idx += 1
                if self._current_target_idx >= len(self._targets):
                    self._phase = "done"
                    self._finish()
                else:
                    self._start_next_point()

    def _start_next_point(self) -> None:
        tx, ty = self._targets[self._current_target_idx]
        self._dot_widget.set_target(tx, ty)
        self._dot_widget.set_progress(0.0)
        self._point_collector.reset()
        self._point_collector.start()

    def _finish(self) -> None:
        self._timer.stop()
        if self._cap:
            self._cap.release()
        if self._landmarker:
            self._landmarker.close()
        self._dot_widget.hide()

        result = {
            "screen_id": self._screen_id,
            "natural_gaze": self._natural_gaze_result,
            "iris_xs": self._calib_iris_xs,
            "iris_ys": self._calib_iris_ys,
            "yaws": self._calib_yaws,
            "pitches": self._calib_pitches,
            "screen_xs_norm": self._calib_screen_xs,
            "screen_ys_norm": self._calib_screen_ys,
        }
        logger.info(f"Calibration wizard finished with {len(self._calib_iris_xs)} points.")
        self.calibration_finished.emit(result)

    def cancel(self) -> None:
        self._timer.stop()
        if self._cap:
            self._cap.release()
        if self._landmarker:
            self._landmarker.close()
        self._dot_widget.hide()
        self.calibration_cancelled.emit()

    def closeEvent(self, event) -> None:
        self.cancel()
        super().closeEvent(event)

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