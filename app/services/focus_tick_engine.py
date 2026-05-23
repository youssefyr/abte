from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import logging

from PySide6.QtCore import QObject, QTimer, Signal

from app.data.entities import FocusTickItem, SessionLogItem
from app.models.focus_model import FocusLightGBMModel
from app.services.focus_feature_extractor import FocusFeatureExtractor, FocusObservation
from app.services.focus_smoother import FocusSmoother, LiveFocusSnapshot

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FocusRuntimeSnapshot:
    timestamp: datetime
    raw_drift_risk: float
    focus_score: float
    model_loaded: bool
    session_id: str | None
    feature_count: int


class FocusTickEngine(QObject):
    focus_updated = Signal(object)
    minute_bucket_closed = Signal(object)
    snapshot_ready = Signal(object)

    def __init__(
        self,
        *,
        repository: Any,
        active_window_service: Any,
        gaze_service: Any | None = None,
        focus_session_service: Any | None = None,
        model_path: str | Path | None = None,
        tick_interval_ms: int = 500,
        model_interval_ticks: int = 4,
        tab_focus_guard=None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._active_window_service = active_window_service
        self._gaze_service = gaze_service
        self._focus_session_service = focus_session_service
        self._tab_focus_guard = tab_focus_guard

        self._extractor = FocusFeatureExtractor(history_minutes=60)
        self._model = FocusLightGBMModel()
        self._smoother = FocusSmoother(max_minutes=60, ema_alpha=None)  # alpha managed adaptively
        self._session_start_time: datetime | None = None  # for adaptive EMA

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(tick_interval_ms)
        self._tick_timer.timeout.connect(self._on_tick)

        self._model_interval_ticks = max(1, model_interval_ticks)
        self._tick_counter = 0
        self._last_raw_risk = 0.5
        self._last_snapshot = FocusRuntimeSnapshot(
            timestamp=datetime.utcnow(),
            raw_drift_risk=0.5,
            focus_score=0.5,
            model_loaded=False,
            session_id=None,
            feature_count=0,
        )

        self._notification_service = None
        self._fact_service = None
        self._slm_service = None
        self._slm_distraction_cache: dict[str, str] = {}  # title -> class cache
        self._data_collection_dir: Path | None = None  # set via set_data_dir()
        
        self._last_nudge_time = datetime.min
        self._last_categorize_time = datetime.min
        self._last_obs = None


        if model_path:
            self._model.load_model(Path(model_path))
        # Gaze is started by on_session_started() and stopped by on_session_ended().
        # Do NOT call attach_session_service here — that creates a duplicate start/stop path,
        # because FocusSessionService.set_gaze_service() already wires the gaze lifecycle.


    def set_notification_service(self, service: Any) -> None:
        self._notification_service = service
        
    def set_fact_service(self, service: Any) -> None:
        self._fact_service = service
        
    def set_slm_service(self, service: Any) -> None:
        self._slm_service = service

    def set_data_dir(self, data_dir: Path) -> None:
        """Enable training data collection. Written as NDJSON to data_dir/training/."""
        self._data_collection_dir = data_dir / "training"
        self._data_collection_dir.mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
        if not self._tick_timer.isActive():
            self._tick_timer.start()

    def stop(self) -> None:
        """Called on app exit — synchronous shutdown."""
        if self._tick_timer.isActive():
            self._tick_timer.stop()
        if self._gaze_service is not None:
            try:
                # Use shutdown() which is the clean synchronous path
                if hasattr(self._gaze_service, "shutdown"):
                    self._gaze_service.shutdown()
            except Exception as exc:
                logger.debug("FocusTickEngine: failed stopping gaze service: %s", exc)

    def current_focus_score(self) -> float:
        return self._smoother.current_focus_score()

    def current_drift_risk(self) -> float:
        return max(0.0, min(1.0, self._last_raw_risk))

    def last_snapshot(self) -> FocusRuntimeSnapshot:
        return self._last_snapshot

    def on_session_started(self, session: SessionLogItem | Any) -> None:
        session_id = getattr(session, "id", None)
        self._smoother.start_session(session_id)
        self._session_start_time = datetime.utcnow()
        # Reset distraction class cache at session start
        self._slm_distraction_cache = {}

        # Start gaze service when a session begins
        if self._gaze_service is not None:
            try:
                if hasattr(self._gaze_service, "start"):
                    self._gaze_service.start()
                    logger.debug("FocusTickEngine: gaze service started for session %s", session_id)
            except Exception as exc:
                logger.warning("FocusTickEngine: failed to start gaze service: %s", exc)

        # Start the tick engine itself if not already running
        if not self._tick_timer.isActive():
            self._tick_timer.start()
            logger.debug("FocusTickEngine: tick timer started for session %s", session_id)

    def on_session_ended(self, session: SessionLogItem | Any) -> None:
        now = datetime.utcnow()
        final_focus = self._smoother.end_session(now)

        # BUG FIX: use _stop_async to avoid blocking the main event loop.
        # The old gaze_service.stop() called worker.wait(3000) on the main thread
        # which prevented any queued signals (including gaze_updated) from processing.
        if self._gaze_service is not None:
            try:
                if hasattr(self._gaze_service, "_stop_async"):
                    self._gaze_service._stop_async()
                    logger.debug("FocusTickEngine: gaze async-stop requested")
            except Exception as exc:
                logger.warning("FocusTickEngine: failed to stop gaze service: %s", exc)

        if session is None or final_focus is None:
            return

        try:
            session.focus_score_avg = float(final_focus)
            meta = dict(getattr(session, "meta", {}) or {})
            meta["focus_score_source"] = "focus_tick_engine"
            meta["focus_score_updated_at"] = now.isoformat()
            session.meta = meta
        except Exception as exc:
            logger.warning("Failed to update session focus score in memory: %s", exc)

    def _on_tick(self) -> None:
        now = datetime.utcnow()

        if self._current_session_id() is None:
            return

        obs = self._capture_observation(now)
        self._last_obs = obs
        self._extractor.record(obs)

        self._tick_counter += 1
        if self._tick_counter >= self._model_interval_ticks:
            self._tick_counter = 0
            features = self._extractor.build_features(obs)
            self._last_raw_risk = self._model.predict_proba(features)
            closed_bucket = self._smoother.update(self._last_raw_risk, now)
            if closed_bucket is not None:
                self._persist_closed_bucket(closed_bucket)
                self.minute_bucket_closed.emit(closed_bucket)
            self._last_snapshot = FocusRuntimeSnapshot(
                timestamp=now,
                raw_drift_risk=self._last_raw_risk,
                focus_score=self._smoother.current_focus_score(),
                model_loaded=self._model.is_loaded,
                session_id=None,
                feature_count=len(features),
            )
            self.focus_updated.emit(self._last_snapshot)
            self.snapshot_ready.emit(self._last_snapshot)  # also emit for plugin consumers

            # Adaptive EMA: responsive for first 5 min, stable afterwards
            if self._session_start_time is not None:
                elapsed_min = (now - self._session_start_time).total_seconds() / 60.0
                alpha = 0.4 if elapsed_min < 5.0 else max(0.10, 0.40 - (elapsed_min - 5.0) * 0.015)
                self._smoother._ema_alpha = alpha

            # Training data collection (#2)
            session_id = self._current_session_id()
            self._collect_observation_record(features, session_id)
            
            # --- Smart Notification Suppression ---
            if self._notification_service and hasattr(self._notification_service, "set_suppressed"):
                if self._last_snapshot.focus_score > 0.8:
                    self._notification_service.set_suppressed(True)
                elif self._last_snapshot.focus_score < 0.6:
                    self._notification_service.set_suppressed(False)
                    
            # --- Context-Aware Nudges ---
            gaze_absent = obs.absent_seconds_estimate > 10.0
            if self._fact_service and self._notification_service:
                from datetime import timedelta
                if self._last_snapshot.focus_score < 0.4 and gaze_absent and now > self._last_nudge_time + timedelta(minutes=15):
                    try:
                        nudge = self._fact_service.get_nudge()
                        if nudge:
                            self._notification_service.publish(
                                title="Focus Coach",
                                message=nudge,
                                level="info",
                            )
                        self._last_nudge_time = now
                    except Exception as e:
                        logger.warning(f"Failed to publish nudge: {e}")
                        
            # --- Staged Distraction Categorization (Idle) ---
            idle = obs.idle_seconds
            if self._slm_service and idle > 10.0:
                from datetime import timedelta
                if now > self._last_categorize_time + timedelta(minutes=5):
                    plan = getattr(self._slm_service, "last_plan", None)
                    latency = getattr(plan, "estimated_latency_seconds", 0.0) if plan else 0.0
                    # Only run background categorization when SLM latency is acceptable.
                    if latency < 5.0:
                        self._last_categorize_time = now
                        self._run_background_categorization()

    def _run_background_categorization(self) -> None:
        """Collect recent unique window titles and ask the SLM to classify them."""
        try:
            history = getattr(self._extractor, "_history", [])
            titles: list[str] = []
            seen: set[str] = set()
            for obs in list(history)[-60:]:  # last 60 observations (~30s)
                t = getattr(obs, "title", "") or ""
                if t and t not in seen and t not in self._slm_distraction_cache:
                    seen.add(t)
                    titles.append(t)
            if titles:
                result = self._slm_service.categorize_distractions(titles)
                if isinstance(result, dict):
                    self._slm_distraction_cache.update(result)
                    logger.debug(
                        "FocusTickEngine: categorized %d window titles via SLM.", len(result)
                    )
        except Exception as exc:
            logger.debug("FocusTickEngine: background categorization failed: %s", exc)

    def _capture_observation(self, now: datetime) -> FocusObservation:
        window_data = {}
        if hasattr(self._active_window_service, "read_active_window"):
            try:
                window_data = self._active_window_service.read_active_window() or {}
                if self._tab_focus_guard is not None and self._focus_session_service is not None:
                    # FIX: active_session is a method, not a property — call it with ()
                    session = self._focus_session_service.active_session()
                    if session is not None:
                        task_title = getattr(session, "planned_task_title", None)
                        if task_title is None and session.planned_task_id and self._repository:
                            task = self._repository.task_by_id(session.planned_task_id)
                            task_title = getattr(task, "title", None) if task else None
                        self._tab_focus_guard.check(task_title)
                    else:
                        self._tab_focus_guard.reset()
            except Exception as exc:
                logger.debug("Active window read failed: %s", exc)

        os_window = None
        if hasattr(self._active_window_service, "get_last_os_window"):
            try:
                os_window = self._active_window_service.get_last_os_window()
            except Exception:
                os_window = None

        gaze_present = False
        face_present = False
        absent_seconds_estimate = 0.0
        yaw_deg = 0.0
        pitch_deg = 0.0
        blink_rate_per_min = 18.0
        eye_open_avg = 0.8
        gaze_zone = "ABSENT"

        if self._gaze_service is not None:
            gaze_present = bool(self._safe_call(self._gaze_service, "is_gaze_present", False))
            face_present = bool(self._safe_call(self._gaze_service, "is_face_present", gaze_present))
            absent_seconds_estimate = float(self._safe_call(self._gaze_service, "absent_seconds", 0.0) or 0.0)
            
            # Fetch latest GazeResult
            gaze_res = self._safe_call(self._gaze_service, "get_last_result", None)
            if gaze_res is not None:
                yaw_deg = float(getattr(gaze_res, "yaw_deg", 0.0) or 0.0)
                pitch_deg = float(getattr(gaze_res, "pitch_deg", 0.0) or 0.0)
                blink_rate_per_min = float(getattr(gaze_res, "blink_rate_per_min", 18.0) or 18.0)
                eye_open_avg = float(getattr(gaze_res, "eye_open_avg", 0.8) or 0.8)
                if getattr(gaze_res, "zone", None) is not None:
                    zone_val = gaze_res.zone
                    gaze_zone = zone_val.name if hasattr(zone_val, "name") else str(zone_val).upper()

        if not face_present:
            gaze_zone = "ABSENT"

        tab_fuzzy_match_score = 100.0
        if self._tab_focus_guard is not None:
            last_score = getattr(self._tab_focus_guard, "last_score", 1.0)
            if last_score is not None:
                tab_fuzzy_match_score = float(last_score * 100.0)

        # Dynamic SLM/productivity classification — prefer cached SLM result, fall back to heuristics
        current_title = str(window_data.get("title", "") or "")
        slm_distraction_class = self._slm_distraction_cache.get(current_title, "")
        if not slm_distraction_class:
            if window_data.get("productive_keyword_hit", False):
                slm_distraction_class = "PRODUCTIVE"
            elif "coding" in str(window_data.get("process_tags", "")) or "terminal" in str(window_data.get("process_tags", "")):
                slm_distraction_class = "PRODUCTIVE"
            elif "browser" in str(window_data.get("process_tags", "")) and not window_data.get("productive_keyword_hit", False):
                if self._tab_focus_guard is not None and getattr(self._tab_focus_guard, "_blocked", False):
                    slm_distraction_class = "DISTRACTING"
                else:
                    slm_distraction_class = "NEUTRAL"
            else:
                slm_distraction_class = "UNKNOWN"

        return FocusObservation(
            timestamp=now,
            process=str(window_data.get("process", getattr(os_window, "process", "")) or ""),
            title=str(window_data.get("title", getattr(os_window, "title", "")) or ""),
            is_browser=bool(getattr(os_window, "is_browser", False)),
            productive_keyword_hit=bool(window_data.get("productive_keyword_hit", False)),
            tab_switch_frequency_5m=float(window_data.get("tab_switch_frequency_5m", 0.0) or 0.0),
            app_switch_frequency_5m=float(window_data.get("app_switch_frequency_5m", 0.0) or 0.0),
            idle_seconds=float(window_data.get("idle_seconds", 0.0) or 0.0),
            gaze_present=gaze_present,
            face_present=face_present,
            absent_seconds_estimate=absent_seconds_estimate,
            focus_score_window_5m=float(window_data.get("focus_score_window_5m", 0.5) or 0.5),
            process_tags=str(window_data.get("process_tags", "") or ""),
            yaw_deg=yaw_deg,
            pitch_deg=pitch_deg,
            blink_rate_per_min=blink_rate_per_min,
            eye_open_avg=eye_open_avg,
            gaze_zone=gaze_zone,
            tab_fuzzy_match_score=tab_fuzzy_match_score,
            slm_distraction_class=slm_distraction_class,
        )

    def _persist_closed_bucket(self, bucket) -> None:
        try:
            if not hasattr(self._repository, "add_focus_tick"):
                return
            item = FocusTickItem(
                id=self._smoother.make_tick_id(),
                started_at=bucket.started_at,
                ended_at=bucket.ended_at,
                p_drift_mean=bucket.p_drift_mean,
                sample_count=bucket.sample_count,
                session_id=bucket.session_id,
                meta=bucket.meta,
            )
            self._repository.add_focus_tick(item)
        except Exception as exc:
            logger.warning("Failed to persist focus tick bucket: %s", exc)

    def _current_session_id(self) -> str | None:
        if self._focus_session_service is None:
            return None
        try:
            active = self._focus_session_service.active_session()
            return getattr(active, "id", None) if active is not None else None
        except Exception:
            return None

    @staticmethod
    def _safe_call(obj: Any, name: str, default: Any) -> Any:
        attr = getattr(obj, name, None)
        if attr is None:
            return default
        try:
            return attr() if callable(attr) else attr
        except Exception:
            return default

    def _collect_observation_record(
        self, features: dict[str, Any], session_id: str | None
    ) -> None:
        """Write one NDJSON training record to disk when data collection is enabled (#2)."""
        if self._data_collection_dir is None:
            return
        try:
            import json as _json
            from datetime import date as _date
            from app.data.entities import FocusObservationRecord
            record = FocusObservationRecord(
                timestamp=datetime.utcnow().isoformat(),
                session_id=session_id,
                drift_label=1 if self._last_raw_risk >= 0.5 else 0,
                raw_drift_risk=float(self._last_raw_risk),
                focus_score=float(self._smoother.current_focus_score()),
                features={k: v for k, v in features.items() if not isinstance(v, str) or len(v) < 64},
            )
            today = _date.today().isoformat()
            out_file = self._data_collection_dir / f"observations_{today}.ndjson"
            line = _json.dumps(record.__dict__) + "\n"
            with open(out_file, "a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception as exc:
            logger.debug("FocusTickEngine: data collection write failed: %s", exc)

    # notify_session_started / notify_session_ended were removed.
    # FocusSessionService now calls on_session_started/ended directly.