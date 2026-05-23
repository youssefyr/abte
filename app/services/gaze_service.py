from __future__ import annotations

import logging
import time
from collections import deque
from pathlib import Path
from typing import Optional, cast

from PySide6.QtCore import QObject, Signal, Qt, QTimer
from PySide6.QtGui import QScreen
from PySide6.QtWidgets import QApplication

from app.core.vision.gaze_mapper import PolynomialGazeMapper
from app.core.vision.gaze_worker import GazeWorker
from app.core.vision.gaze_zone_classifier import GazeResult, GazeZone
from app.calibration.calibration_store import CalibrationStore

logger = logging.getLogger(__name__)


class GazeService(QObject):
    gaze_updated = Signal(object)  # GazeResult
    camera_status = Signal(str, str)
    calibration_decay_detected = Signal()
    fatigue_warning = Signal()

    def __init__(
        self,
        model_path: str | Path,
        data_dir: Path,
        camera_index: int = 0,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._model_path = Path(model_path)
        self._data_dir = data_dir
        self._camera_index = camera_index
        self._enabled = False

        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._mapper = PolynomialGazeMapper()
        self._store = CalibrationStore(data_dir)
        self._worker: Optional[GazeWorker] = None
        self._last_result: Optional[GazeResult] = None
        self._last_result_ts: Optional[float] = None
        self._history: deque[tuple[float, GazeResult]] = deque(maxlen=6000)
        self._camera_blocked_until: float = 0.0
        self._last_camera_status: tuple[str, str] | None = None

        # Face presence tracking (used for absent_seconds — face-based, not zone-based)
        self._last_present_ts: float | None = None
        self._first_result_ts: float | None = None

        self._noise_events: int = 0
        self._fatigue_cooldown: float = 0.0
        self._winding_down_workers: list = []  # holds refs to stopping workers to prevent GC

        self._restore_calibrations()

    # -----------------------------------------------------------------------
    # Enable / disable
    # -----------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not enabled and self.is_running():
            self._stop_async()

    def is_enabled(self) -> bool:
        return self._enabled

    # -----------------------------------------------------------------------
    # Session lifecycle wiring
    # -----------------------------------------------------------------------

    def attach_session_service(self, session_service: QObject) -> None:
        """
        Wire gaze lifecycle to a FocusSessionService.

        IMPORTANT: In the current ABTE wiring, this method should NOT be called if
        FocusSessionService.set_gaze_service() has already been used to wire the
        lifecycle — that path calls start()/stop() directly via on_session_started/ended.
        Calling both creates a double-start bug (gaze worker spawned twice, camera
        opened twice). This guard detects and blocks the duplicate wiring at runtime
        instead of relying on a comment.
        """
        started_signal = getattr(session_service, "session_started", None)
        if started_signal is not None:
            try:
                # PySide6 exposes receiver count; >0 means something is already connected.
                if started_signal.receivers(started_signal) > 0:
                    logger.warning(
                        "GazeService.attach_session_service(): session_started already has "
                        "receivers — skipping to prevent double-start bug. Use only one "
                        "wiring path (set_gaze_service OR attach_session_service, not both)."
                    )
                    return
            except Exception:
                pass  # receivers() may not be available in all PySide6 versions

        for signal, slot in [
            (session_service.session_started, self._on_session_started),  # type: ignore[attr-defined]
            (session_service.session_ended, self._on_session_ended),      # type: ignore[attr-defined]
        ]:
            try:
                signal.connect(slot, Qt.ConnectionType.UniqueConnection)
            except (RuntimeError, TypeError):
                pass

    def _on_session_started(self, _session: object) -> None:
        if self._enabled:
            self.start()

    def _on_session_ended(self, _session: object) -> None:
        self._stop_async()

    # -----------------------------------------------------------------------
    # Start / stop
    # -----------------------------------------------------------------------

    def start(self) -> None:
        if not self._enabled:
            logger.info("GazeService start skipped (disabled).")
            return
        if time.time() < self._camera_blocked_until:
            logger.info("GazeService start skipped (camera busy).")
            return
        if self._worker and self._worker.isRunning():
            return
        if not self._model_path.exists() or not self._model_path.is_file():
            self._last_camera_status = ("unavailable", "model_missing")
            self.camera_status.emit("unavailable", "model_missing")
            logger.warning("GazeService start skipped (model missing: %s).", self._model_path)
            return

        self._worker = GazeWorker(
            model_path=self._model_path,
            camera_index=self._camera_index,
            parent=None,
        )
        self._worker.set_mapper(self._mapper)

        screen_id = self._primary_screen_id()
        self._apply_neutral_to_worker(screen_id)
        w, h = self._screen_size(screen_id)
        self._worker.set_active_screen(screen_id, w, h)

        self._worker.gaze_result.connect(self._on_gaze_result)
        self._worker.camera_status.connect(self._on_camera_status)
        self._worker.start()
        logger.info("GazeService started.")

    def stop(self) -> None:
        """Synchronous stop — only call from the worker thread or shutdown paths."""
        self._do_stop()
        # Also clean up and wait for any winding-down workers to prevent GC SIGSEGV/SIGABRT on shutdown
        for worker in list(getattr(self, "_winding_down_workers", [])):
            try:
                if worker.isRunning():
                    worker.stop_request()
                    worker.wait(3000)
            except Exception:
                pass
        self._winding_down_workers = []

    def _stop_async(self) -> None:
        """
        Non-blocking stop for use from the main Qt thread.
        Signals the worker to stop and lets it wind down cleanly without blocking.
        """
        if not self._worker:
            return
        worker = self._worker
        self._worker = None
        
        if not hasattr(self, "_winding_down_workers"):
            self._winding_down_workers = []
        self._winding_down_workers.append(worker)

        worker.stop_request()

        # State tracking to ensure cleanup runs exactly once and avoids use-after-free
        state = {"cleaned_up": False}

        def _cleanup() -> None:
            if state["cleaned_up"]:
                return
            state["cleaned_up"] = True

            try:
                try:
                    worker.finished.disconnect(_cleanup)
                except Exception:
                    pass

                if worker.isRunning():
                    logger.debug("GazeWorker: waiting cleanly for thread to wind down...")
                    worker.wait(3000)
            except Exception as e:
                logger.debug("Error in gaze worker cleanup: %s", e)
            finally:
                try:
                    if worker in self._winding_down_workers:
                        self._winding_down_workers.remove(worker)
                except Exception as e:
                    logger.debug("Error removing worker from winding down list: %s", e)

        worker.finished.connect(_cleanup)
        QTimer.singleShot(3000, _cleanup)
        logger.info("GazeService: async stop requested.")

    def _do_stop(self) -> None:
        if self._worker:
            worker = self._worker
            self._worker = None
            worker.stop_request()
            if worker.isRunning():
                worker.wait(3000)
                if worker.isRunning():
                    logger.warning("GazeService stop timed out; worker still running.")
        logger.info("GazeService stopped.")

    def configure(self, model_path: str | Path, camera_index: int) -> None:
        restart = self.is_running()
        if restart:
            self._do_stop()
        self._model_path = Path(model_path)
        self._camera_index = max(0, int(camera_index or 0))
        if restart and self._enabled:
            self.start()

    def shutdown(self) -> None:
        """Synchronous full shutdown — drains winding-down workers to prevent GC SIGSEGV."""
        self.set_enabled(False)
        self._do_stop()
        # Wait for any winding-down workers to finish before the event loop tears down.
        for worker in list(getattr(self, "_winding_down_workers", [])):
            try:
                if worker.isRunning():
                    worker.stop_request()
                    worker.wait(3000)
            except Exception:
                pass
        self._winding_down_workers = []

    def is_running(self) -> bool:
        return bool(self._worker and self._worker.isRunning())

    # -----------------------------------------------------------------------
    # State queries
    # -----------------------------------------------------------------------

    def is_face_present(self) -> bool:
        return bool(self._last_result and self._last_result.face_detected)

    def is_gaze_present(self) -> bool:
        if not self._last_result:
            return False
        return self._last_result.zone not in {GazeZone.ABSENT, GazeZone.NOT_CALIBRATED}

    def absent_seconds(self) -> float:
        """Seconds since the face was last seen by the camera."""
        if self._last_result and self._last_result.face_detected:
            return 0.0
        if not self._last_present_ts:
            if self._first_result_ts:
                return max(0.0, time.time() - self._first_result_ts)
            return 0.0
        return max(0.0, time.time() - self._last_present_ts)

    # -----------------------------------------------------------------------
    # Calibration
    # -----------------------------------------------------------------------

    def apply_calibration_result(
        self,
        screen_id: str,
        iris_xs: list[float],
        iris_ys: list[float],
        yaws: list[float],
        pitches: list[float],
        screen_xs_norm: list[float],
        screen_ys_norm: list[float],
        natural_gaze: dict,
    ) -> dict:
        self._store.save_natural_gaze(
            screen_id=screen_id,
            neutral_iris_x=natural_gaze["neutral_iris_x"],
            neutral_iris_y=natural_gaze["neutral_iris_y"],
            neutral_yaw=natural_gaze["neutral_yaw"],
            neutral_pitch=natural_gaze["neutral_pitch"],
        )

        metrics = self._mapper.fit(
            screen_id=screen_id,
            iris_xs=iris_xs,
            iris_ys=iris_ys,
            yaws=yaws,
            pitches=pitches,
            screen_xs_norm=screen_xs_norm,
            screen_ys_norm=screen_ys_norm,
        )

        coeffs = self._mapper.export_coefficients(screen_id)
        self._store.save_mapper_coefficients(screen_id, coeffs)

        if self._worker:
            self._worker.set_mapper(self._mapper)
            self._apply_neutral_to_worker(screen_id)

        return metrics

    def get_last_result(self) -> Optional[GazeResult]:
        return self._last_result

    def read_presence(self) -> dict:
        now = time.time()
        cutoff = now - 300.0
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

        present = False
        drift_rate = 0.0
        last_zone = None
        if self._last_result is not None:
            last_zone = self._last_result.zone.value
            present = self._last_result.zone not in {GazeZone.ABSENT, GazeZone.NOT_CALIBRATED}

        if self._history:
            off_count = sum(
                1 for _ts, res in self._history
                if res.zone in {GazeZone.OFF_SCREEN, GazeZone.LOOKING_AWAY}
            )
            drift_rate = off_count / max(len(self._history), 1)

            if drift_rate > 0.4 and now > self._fatigue_cooldown:
                self.fatigue_warning.emit()
                self._fatigue_cooldown = now + 600.0  # 10 minutes cooldown

        return {
            "present": present,
            "drift_rate_5m": drift_rate,
            "last_zone": last_zone,
            "last_seen": self._last_result_ts,
            "calibrated": self.is_calibrated(),
        }

    def is_calibrated(self, screen_id: Optional[str] = None) -> bool:
        if screen_id is None:
            screen_id = self._primary_screen_id()
        return self._mapper.is_calibrated(screen_id)

    def set_active_screen(self, screen_id: str) -> None:
        if self._worker:
            w, h = self._screen_size(screen_id)
            self._worker.set_active_screen(screen_id, w, h)
            self._apply_neutral_to_worker(screen_id)
            self._mapper.reset_kalman()

    # -----------------------------------------------------------------------
    # Internal signal handlers
    # -----------------------------------------------------------------------

    def _on_gaze_result(self, result: GazeResult) -> None:
        self._last_result = result
        ts = time.time()
        if self._first_result_ts is None:
            self._first_result_ts = ts
        self._last_result_ts = ts

        # Track presence by face detection (not by gaze zone) so that the
        # absence timer works correctly even on an uncalibrated system.
        if result.face_detected:
            self._last_present_ts = ts

        self._history.append((ts, result))

        noise = getattr(result, 'calibration_noise', 0.0)
        if noise > 0.15:
            self._noise_events += 1
            if self._noise_events > 50:  # ~5 seconds
                self.calibration_decay_detected.emit()
                self._noise_events = 0
        else:
            self._noise_events = max(0, self._noise_events - 1)

        self.gaze_updated.emit(result)

    def _on_camera_status(self, status: str, detail: str) -> None:
        self._last_camera_status = (status, detail)
        self.camera_status.emit(status, detail)
        if status in {"unavailable", "lost"}:
            self._camera_blocked_until = time.time() + 30.0
            logger.warning("GazeService: camera %s (%s).", status, detail)
            # BUG FIX: use async stop to avoid blocking the main thread
            # when the worker itself emits this signal
            self._stop_async()

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _apply_neutral_to_worker(self, screen_id: str) -> None:
        if self._worker is None:
            return
        ng = self._store.load_natural_gaze(screen_id)
        if ng:
            self._worker.set_neutral(
                iris_x=ng["neutral_iris_x"],
                iris_y=ng["neutral_iris_y"],
                yaw=ng["neutral_yaw"],
                pitch=ng["neutral_pitch"],
            )

    def _restore_calibrations(self) -> None:
        for screen_id in self._store.all_screen_ids():
            coeffs = self._store.load_mapper_coefficients(screen_id)
            if coeffs:
                try:
                    self._mapper.import_coefficients(screen_id, coeffs)
                    logger.info(f"GazeService: restored calibration for {screen_id}")
                except Exception as exc:
                    logger.warning(f"Failed to restore calibration for {screen_id}: {exc}")

    def _primary_screen_id(self) -> str:
        app = cast(Optional[QApplication], QApplication.instance())
        if app:
            screen = app.primaryScreen()
            if screen:
                return self._screen_fingerprint(screen)
        return "primary"

    @staticmethod
    def _screen_fingerprint(screen: QScreen) -> str:
        g = screen.geometry()
        return f"{screen.name()}_{g.width()}x{g.height()}"

    def _screen_size(self, screen_id: str) -> tuple[int, int]:
        app = cast(Optional[QApplication], QApplication.instance())
        if app:
            for screen in app.screens():
                if self._screen_fingerprint(screen) == screen_id:
                    g = screen.geometry()
                    return g.width(), g.height()
        return 1920, 1080