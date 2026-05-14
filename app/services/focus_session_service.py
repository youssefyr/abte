from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
import uuid
from typing import Any

from PySide6.QtCore import QObject, Signal

from app.data.entities import SessionLogItem
from app.services.handle_tasks import TaskService

logger = logging.getLogger(__name__)


class FocusSessionService(QObject):
    session_changed = Signal(object)
    session_started = Signal(object)
    session_paused  = Signal(object)
    session_resumed = Signal(object)
    session_ended   = Signal(object)

    def __init__(
        self,
        repository: Any,
        focus_tick_engine: Any | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self._focus_tick_engine = focus_tick_engine
        self._gaze_service = None

        # In-memory cache so _on_gaze_updated (10 Hz) doesn't hit the DB
        self._active_session_cache: SessionLogItem | None = None

    # -----------------------------------------------------------------------
    # Service wiring
    # -----------------------------------------------------------------------

    def set_focus_tick_engine(self, engine: Any | None) -> None:
        self._focus_tick_engine = engine

    def set_gaze_service(self, gaze_service: Any) -> None:
        if self._gaze_service is gaze_service:
            return  # already wired — prevent duplicate connections
        self._gaze_service = gaze_service
        if self._gaze_service:
            try:
                self._gaze_service.gaze_updated.connect(self._on_gaze_updated)
            except Exception as exc:
                logger.warning("set_gaze_service: connect failed: %s", exc)

    # -----------------------------------------------------------------------
    # Gaze auto-pause / auto-resume (runs at ~10 Hz via gaze_updated signal)
    # -----------------------------------------------------------------------

    def _on_gaze_updated(self, result: Any) -> None:
        if not self._gaze_service:
            return

        # Use in-memory cache — NOT a DB query — to keep this hot path fast
        session = self._active_session_cache
        if not session:
            return

        absent_sec   = float(getattr(self._gaze_service, "absent_seconds", lambda: 0.0)())
        face_present = bool(getattr(self._gaze_service, "is_face_present", lambda: False)())

        # Auto-pause: face gone for more than 60 continuous seconds
        if str(session.outcome) == "running" and not face_present and absent_sec > 60.0:
            logger.info(
                "GazeService: face absent for %.0fs — auto-pausing session.", absent_sec
            )
            self.pause_session(reason="auto_pause_absent")

        # Auto-resume: face returned — only if WE were the ones who paused it
        elif str(session.outcome) == "paused":
            meta = dict(session.meta or {})
            if meta.get("pause_reason") == "auto_pause_absent" and face_present:
                logger.info("GazeService: face returned — auto-resuming session.")
                self.resume_session()

    # -----------------------------------------------------------------------
    # Session queries (public)
    # -----------------------------------------------------------------------

    def list_sessions(self) -> list[SessionLogItem]:
        return self._all_sessions()

    def clear_all_sessions(self) -> None:
        if hasattr(self.repository, "clear_all_sessions"):
            self.repository.clear_all_sessions()
        self._active_session_cache = None

    def active_session(self) -> SessionLogItem | None:
        # Return cache if still valid
        if self._active_session_cache is not None:
            # Validate that it is still open (ended_at still None and outcome open)
            s = self._active_session_cache
            if s.ended_at is None and str(getattr(s, "outcome", "")) in {"running", "paused"}:
                return s
            # Cache is stale — clear it and fall through to DB
            self._active_session_cache = None

        # DB fallback (only when cache is invalid/missing)
        sessions = self._all_sessions()
        for session in sessions:
            if session.ended_at is None and str(getattr(session, "outcome", "")) in {"running", "paused"}:
                self._active_session_cache = session
                return session
        return None

    # -----------------------------------------------------------------------
    # Session lifecycle mutations
    # -----------------------------------------------------------------------

    def start_session(
        self,
        *,
        planned_task_id: str | None = None,
        mode: str = "focus",
        source: str = "manual",
    ) -> SessionLogItem:
        existing = self.active_session()
        if existing is not None:
            return existing

        now = datetime.utcnow()
        session = SessionLogItem(
            id=f"session-{uuid.uuid4().hex[:12]}",
            started_at=now,
            ended_at=None,
            mode=mode,
            planned_task_id=planned_task_id,
            outcome="running",
            focus_score_avg=None,
            distraction_events=0,
            absent_seconds=0,
            meta={
                "state": "running",
                "source": source,
                "paused_seconds": 0,
                "started_at": now.isoformat(),
            },
        )
        saved = self.repository.add_session(session)
        self._active_session_cache = saved

        # Notify tick engine (starts gaze, starts smoother)
        if self._focus_tick_engine is not None:
            try:
                self._focus_tick_engine.on_session_started(saved)
            except Exception as exc:
                logger.warning("FocusTickEngine.on_session_started failed: %s", exc)

        self.session_started.emit(saved)
        self.session_changed.emit(saved)
        return saved

    def pause_session(self, *, reason: str = "manual") -> SessionLogItem | None:
        session = self.active_session()
        if session is None or str(session.outcome) != "running":
            return None

        now = datetime.utcnow()
        meta = dict(session.meta or {})
        paused_seconds = int(meta.get("paused_seconds", 0) or 0)
        meta["state"]          = "paused"
        meta["paused_at"]      = now.isoformat()
        meta["pause_reason"]   = reason
        meta["paused_seconds"] = paused_seconds

        updated = replace(session, outcome="paused", meta=meta)
        updated = self.repository.add_session(updated)
        self._active_session_cache = updated
        self.session_paused.emit(updated)
        self.session_changed.emit(updated)
        return updated

    def resume_session(self) -> SessionLogItem | None:
        session = self.active_session()
        if session is None or str(session.outcome) != "paused":
            return None

        now = datetime.utcnow()
        meta = dict(session.meta or {})
        paused_at      = self._parse_meta_dt(meta.get("paused_at"))
        paused_seconds = int(meta.get("paused_seconds", 0) or 0)
        if paused_at is not None:
            paused_seconds += max(0, int((now - paused_at).total_seconds()))
        meta["paused_seconds"] = paused_seconds
        meta["paused_at"]      = None
        meta["state"]          = "running"
        meta["pause_reason"]   = None

        updated = replace(session, outcome="running", meta=meta)
        updated = self.repository.add_session(updated)
        self._active_session_cache = updated
        self.session_resumed.emit(updated)
        self.session_changed.emit(updated)
        return updated

    def stop_session(self, *, outcome: str = "stopped") -> SessionLogItem | None:
        session = self.active_session()
        if session is None:
            return None

        now = datetime.utcnow()
        meta = dict(session.meta or {})
        paused_at      = self._parse_meta_dt(meta.get("paused_at"))
        paused_seconds = int(meta.get("paused_seconds", 0) or 0)
        if paused_at is not None:
            paused_seconds += max(0, int((now - paused_at).total_seconds()))
        meta["paused_seconds"] = paused_seconds
        meta["paused_at"]      = None
        meta["state"]          = outcome

        updated = replace(session, ended_at=now, outcome=outcome, meta=meta)
        updated = self.repository.add_session(updated)
        # Clear cache BEFORE emitting signals so gaze handler sees no active session
        self._active_session_cache = None

        if self._focus_tick_engine is not None:
            try:
                self._focus_tick_engine.on_session_ended(updated)
                refreshed = self.repository.add_session(updated)
                updated = refreshed
            except Exception as exc:
                logger.warning("FocusTickEngine.on_session_ended failed: %s", exc)

        self.session_ended.emit(updated)
        self.session_changed.emit(updated)
        return updated

    def complete_session(
        self,
        *,
        mark_task_done: bool = False,
        task_service: TaskService | None = None,
    ) -> SessionLogItem | None:
        updated = self.stop_session(outcome="completed")
        if updated is None:
            return None
        if mark_task_done and updated.planned_task_id and task_service is not None:
            try:
                task_service.set_status(updated.planned_task_id, "done")
            except Exception:
                pass
        return updated

    # -----------------------------------------------------------------------
    # Elapsed time helper
    # -----------------------------------------------------------------------

    def elapsed_seconds(self, session: SessionLogItem | None) -> int:
        if session is None or session.started_at is None:
            return 0
        now = datetime.utcnow()
        end = session.ended_at or now
        meta = dict(session.meta or {})
        paused_seconds = int(meta.get("paused_seconds", 0) or 0)
        paused_at = self._parse_meta_dt(meta.get("paused_at"))
        if paused_at is not None:
            paused_seconds += max(0, int((now - paused_at).total_seconds()))
        total = max(0, int((end - session.started_at).total_seconds()) - paused_seconds)
        return total

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _all_sessions(self) -> list[SessionLogItem]:
        if hasattr(self.repository, "all_sessions"):
            return list(self.repository.all_sessions())
        return []

    def _parse_meta_dt(self, value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None
        return None