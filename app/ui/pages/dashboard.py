from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, cast

from shiboken6 import isValid
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.focus_session_service import FocusSessionService
from app.services.handle_tasks import TaskService
from app.services.planner_service import PlannerService
from app.services.focus_tick_engine import FocusTickEngine, FocusRuntimeSnapshot as LiveFocusSnapshot
from app.ui.calendar_widget import FlexibleWeekViewWidget
from app.ui.pages.base_page import BasePage
from app.ui.ui_helpers import make_button, make_card, make_kpi_tile, make_label, make_pill, make_section_header


class FocusRingWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = 0
        self.setMinimumSize(132, 132)

    def setValue(self, value: int) -> None:
        self._value = max(0, min(100, int(value)))
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(10, 10, -10, -10)
        start_angle = 90 * 16
        span = int(-360 * 16 * (self._value / 100.0))

        palette = self.palette()
        text_color = palette.text().color()
        muted_color = palette.placeholderText().color()
        accent_color = palette.highlight().color()

        track_color = QColor(text_color)
        track_color.setAlpha(28)
        track_pen = QPen(track_color, 10)
        painter.setPen(track_pen)
        painter.drawArc(rect, 0, 360 * 16)

        active_pen = QPen(QColor(accent_color), 10)
        active_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(active_pen)
        painter.drawArc(rect, start_angle, span)

        value_text = str(self._value)
        value_font = painter.font()
        value_font.setPointSize(20)
        value_font.setBold(True)
        label_font = painter.font()
        label_font.setPointSize(8)
        label_font.setBold(False)

        value_metrics = QFontMetrics(value_font)
        label_metrics = QFontMetrics(label_font)

        total_height = value_metrics.height() + label_metrics.height() + 2
        center_y = rect.center().y()
        value_y = center_y - (total_height / 2) + value_metrics.ascent()
        label_y = value_y + label_metrics.height() + 2

        painter.setFont(value_font)
        painter.setPen(QColor(text_color))
        painter.drawText(QRectF(rect.left(), value_y - value_metrics.ascent(), rect.width(), value_metrics.height()), Qt.AlignmentFlag.AlignHCenter, value_text)

        painter.setFont(label_font)
        painter.setPen(QColor(muted_color))
        painter.drawText(QRectF(rect.left(), label_y - label_metrics.ascent(), rect.width(), label_metrics.height()), Qt.AlignmentFlag.AlignHCenter, "FOCUS")
        painter.end()


class DashboardPage(BasePage):
    def __init__(self, metrics, repository: Any, focus_session_service: FocusSessionService | None = None, focus_tick_engine: FocusTickEngine | None = None, parent=None):
        super().__init__(metrics, parent)
        self.repository = repository
        self.task_service = TaskService(repository)
        self.planner_service = PlannerService(repository)
        self.focus_session_service = focus_session_service
        self.focus_tick_engine = focus_tick_engine
        self._session_timer = QTimer(self)
        self._session_timer.setInterval(1000)
        self._session_timer.timeout.connect(self._update_session_clock)
        self._target_minutes: int | None = None  # None = open-ended session
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(20)

        self.labels_to_search: list[Any] = []
        self._today_rows: list[QFrame] = []
        self._signal_rows: list[QFrame] = []
        self._static_labels: list[Any] = []

        self._build_ui()

        self.start_focus_btn.clicked.connect(self._start_focus_session)
        self.generate_plan_btn.clicked.connect(self._open_planner)
        self.session_start_btn.clicked.connect(self._start_focus_session)
        self.session_pick_btn.clicked.connect(self._start_focus_session_with_picker)
        self.session_plan_btn.clicked.connect(self._start_focus_session_with_planner)
        self.session_pause_btn.clicked.connect(self._pause_focus_session)
        self.session_resume_btn.clicked.connect(self._resume_focus_session)
        self.session_stop_btn.clicked.connect(self._stop_focus_session)
        self.session_complete_btn.clicked.connect(self._complete_focus_session)

        if hasattr(self.repository, "tasks_changed"):
            self.repository.tasks_changed.connect(self.refresh_data)
        if hasattr(self.repository, "notifications_changed"):
            self.repository.notifications_changed.connect(self.refresh_data)
        if hasattr(self.repository, "sessions_changed"):
            self.repository.sessions_changed.connect(self.refresh_data)

        if self.focus_tick_engine is not None:
            self.focus_tick_engine.focus_updated.connect(self._on_live_focus_tick)

        self._session_timer.start()
        self.refresh_data()

    def _build_ui(self) -> None:
        self.hero_card, hero_layout = make_card("", "", elevated=True)
        hero_root = QHBoxLayout()
        hero_root.setContentsMargins(0, 0, 0, 0)
        hero_root.setSpacing(22)

        self.focus_ring = FocusRingWidget()
        hero_root.addWidget(self.focus_ring, 0, Qt.AlignmentFlag.AlignTop)

        hero_text = QVBoxLayout()
        hero_text.setContentsMargins(0, 0, 0, 0)
        hero_text.setSpacing(10)
        self.hero_eyebrow = make_label("TODAY", "meta")
        self.hero_title = make_label("Good evening, ready for a focused block?", "pageTitle", True)
        self.hero_subtitle = make_label("Your strongest focus window is approaching. Keep the next work block small and concrete.", "muted", True)
        hero_actions = QHBoxLayout()
        hero_actions.setContentsMargins(0, 0, 0, 0)
        hero_actions.setSpacing(10)
        self.start_focus_btn = make_button("Start focus session", "primary")
        self.generate_plan_btn = make_button("Generate plan", "secondary")
        hero_actions.addWidget(self.start_focus_btn)
        hero_actions.addWidget(self.generate_plan_btn)
        hero_actions.addStretch(1)

        hero_text.addWidget(self.hero_eyebrow)
        hero_text.addWidget(self.hero_title)
        hero_text.addWidget(self.hero_subtitle)
        hero_text.addLayout(hero_actions)
        hero_text.addStretch(1)
        hero_root.addLayout(hero_text, 1)
        hero_layout.addLayout(hero_root)
        self.main_layout.addWidget(self.hero_card)

        kpi_row = QHBoxLayout()
        kpi_row.setContentsMargins(0, 0, 0, 0)
        kpi_row.setSpacing(14)
        self.kpi_load, self.kpi_load_value, self.kpi_load_delta = make_kpi_tile("TODAY'S LOAD", "0h 00m", "+0%", tone="good")
        self.kpi_focus, self.kpi_focus_value, self.kpi_focus_delta = make_kpi_tile("FOCUS SCORE", "0", "+0%", tone="good")
        self.kpi_streak, self.kpi_streak_value, self.kpi_streak_delta = make_kpi_tile("STREAK", "0", "Keep going", tone="default")
        self.kpi_unscheduled, self.kpi_unscheduled_value, self.kpi_unscheduled_delta = make_kpi_tile("UNSCHEDULED", "0", "Clear", tone="default")
        for tile in [self.kpi_load, self.kpi_focus, self.kpi_streak, self.kpi_unscheduled]:
            kpi_row.addWidget(tile, 1)
        self.main_layout.addLayout(kpi_row)

        lower = QHBoxLayout()
        lower.setContentsMargins(0, 0, 0, 0)
        lower.setSpacing(16)

        self.session_card, session_layout = make_card("Current session", "Fast status, no clutter.", elevated=False, eyebrow="LIVE")
        self.session_card.setObjectName("SessionCard")
        self.session_status_pill = make_pill("Ready", "default")
        session_layout.addWidget(self.session_status_pill, 0, Qt.AlignmentFlag.AlignLeft)

        self.current_task_title = make_label("No active task", "sectionTitle")
        self.current_task_subtitle = make_label("The next scheduled item will appear here.", "muted", True)
        self.current_timer = make_label("00:00", "pageTitle")
        self.current_timer.setProperty("role", "mono")

        timer_row = QHBoxLayout()
        timer_row.setContentsMargins(0, 0, 0, 0)
        timer_row.setSpacing(12)
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(6)
        text_col.addWidget(self.current_task_title)
        text_col.addWidget(self.current_task_subtitle)
        timer_row.addLayout(text_col, 1)
        timer_row.addWidget(self.current_timer, 0, Qt.AlignmentFlag.AlignVCenter)
        session_layout.addLayout(timer_row)

        controls = QVBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)

        # Duration picker row (Pomodoro mode) — shown before the Start button
        duration_row = QHBoxLayout()
        duration_row.setContentsMargins(0, 0, 0, 0)
        duration_row.setSpacing(8)
        duration_label = make_label("Duration:", "muted")
        self.duration_picker = QComboBox()
        self.duration_picker.setObjectName("DurationPicker")
        self.duration_picker.addItem("Open-ended", 0)
        self.duration_picker.addItem("25 min (Pomodoro)", 25)
        self.duration_picker.addItem("45 min", 45)
        self.duration_picker.addItem("90 min", 90)
        self.duration_picker.setToolTip("Set a target session duration. A notification fires and session auto-pauses when reached.")
        duration_row.addWidget(duration_label)
        duration_row.addWidget(self.duration_picker)
        duration_row.addStretch(1)
        controls.addLayout(duration_row)

        primary_row = QHBoxLayout()
        primary_row.setContentsMargins(0, 0, 0, 0)
        primary_row.setSpacing(8)
        self.session_start_btn = make_button("Start", "primary")
        self.session_start_btn.setObjectName("SessionPrimaryButton")
        self.session_pick_btn = make_button("Pick task", "secondary")
        self.session_pick_btn.setObjectName("SessionSecondaryButton")
        self.session_plan_btn = make_button("Use planner", "ghost")
        self.session_plan_btn.setObjectName("SessionGhostButton")
        primary_row.addWidget(self.session_start_btn)
        primary_row.addWidget(self.session_pick_btn)
        primary_row.addWidget(self.session_plan_btn)
        primary_row.addStretch(1)
        controls.addLayout(primary_row)

        secondary_row = QHBoxLayout()
        secondary_row.setContentsMargins(0, 0, 0, 0)
        secondary_row.setSpacing(8)
        self.session_pause_btn = make_button("Pause", "secondary")
        self.session_pause_btn.setObjectName("SessionSecondaryButton")
        self.session_resume_btn = make_button("Resume", "secondary")
        self.session_resume_btn.setObjectName("SessionSecondaryButton")
        self.session_stop_btn = make_button("Stop", "ghost")
        self.session_stop_btn.setObjectName("SessionGhostButton")
        self.session_complete_btn = make_button("Complete", "secondary")
        self.session_complete_btn.setObjectName("SessionSecondaryButton")
        secondary_row.addWidget(self.session_pause_btn)
        secondary_row.addWidget(self.session_resume_btn)
        secondary_row.addWidget(self.session_stop_btn)
        secondary_row.addWidget(self.session_complete_btn)
        secondary_row.addStretch(1)
        controls.addLayout(secondary_row)

        session_layout.addLayout(controls)

        self.progress_track = QFrame()
        self.progress_track.setObjectName("Card")
        progress_track_layout = QVBoxLayout(self.progress_track)
        progress_track_layout.setContentsMargins(0, 0, 0, 0)
        progress_track_layout.setSpacing(0)
        self.progress_fill = QFrame()
        self.progress_fill.setObjectName("FocusProgressFill")
        self.progress_fill.setFixedHeight(8)
        progress_track_layout.addWidget(self.progress_fill)
        session_layout.addWidget(self.progress_track)

        up_next_header, _ = make_section_header("Up next", "Only the next relevant items.")
        session_layout.addWidget(up_next_header)
        self.up_next_host = QWidget()
        self.up_next_layout = QVBoxLayout(self.up_next_host)
        self.up_next_layout.setContentsMargins(0, 0, 0, 0)
        self.up_next_layout.setSpacing(10)
        session_layout.addWidget(self.up_next_host)

        self.week_card, week_layout = make_card("This week", "Compact planner preview.", elevated=False)
        self.mini_calendar = FlexibleWeekViewWidget(repository=self.repository, compact=True)
        week_layout.addWidget(self.mini_calendar)

        lower.addWidget(self.session_card, 2)
        lower.addWidget(self.week_card, 1)
        self.main_layout.addLayout(lower)

        signals_header, _ = make_section_header("Signals", "Intervention and trend summary.")
        self.main_layout.addWidget(signals_header)
        self.signal_grid = QHBoxLayout()
        self.signal_grid.setContentsMargins(0, 0, 0, 0)
        self.signal_grid.setSpacing(14)
        self.main_layout.addLayout(self.signal_grid)

    def refresh_data(self) -> None:
        tasks = self.task_service.list_tasks()
        sessions = self._recent_sessions()
        today = date.today()
        today_tasks = self.task_service.tasks_for_day(today)
        done_today = sum(1 for task in today_tasks if getattr(task, "status", "todo") == "done")
        total_today = len(today_tasks)
        remaining_today = sum(1 for task in today_tasks if getattr(task, "status", "todo") != "done")
        task_progress = int((done_today / total_today) * 100) if total_today else 0

        # Focus ring: prefer live engine score, fall back to task-completion progress.
        if self.focus_tick_engine is not None:
            live_focus_int = max(0, min(100, int(round(
                self.focus_tick_engine.current_focus_score() * 100
            ))))
            self.focus_ring.setValue(live_focus_int)
        else:
            self.focus_ring.setValue(task_progress if total_today else 58)

        self.hero_eyebrow.setText(datetime.now().strftime("%A, %b %d").upper())
        self.hero_title.setText(self._dynamic_greeting())
        self.hero_subtitle.setText(
            f"Your historically strongest cluster starts at {self._strongest_hour_text()}. "
            f"{remaining_today} active tasks still need attention today."
        )

        total_estimated = sum(int(getattr(task, "estimated_minutes", 30) or 30) for task in today_tasks)
        load_hours = total_estimated // 60
        load_minutes = total_estimated % 60
        self.kpi_load_value.setText(f"{load_hours}h {load_minutes:02d}m")

        # Focus KPI: prefer live engine score, fall back to historical average.
        if self.focus_tick_engine is not None:
            focus_score = max(0, min(100, int(round(
                self.focus_tick_engine.current_focus_score() * 100
            ))))
        else:
            focus_score = self._focus_score_from_sessions(sessions, task_progress)
        self.kpi_focus_value.setText(str(focus_score))

        self.kpi_streak_value.setText(str(self._compute_streak(tasks)))
        self.kpi_unscheduled_value.setText(str(sum(
            1 for t in tasks
            if getattr(t, "scheduled_start", None) is None
            and getattr(t, "status", "todo") != "done"
        )))

        live_session = (
            self.focus_session_service.active_session()
            if self.focus_session_service is not None
            else self._latest_live_session(sessions)
        )
        self.session_card.setProperty("active", "true" if live_session is not None else "false")
        self.session_card.style().unpolish(self.session_card)
        self.session_card.style().polish(self.session_card)
        next_task = self._pick_current_task(today_tasks, tasks, live_session)

        if live_session is not None:
            outcome = str(getattr(live_session, "outcome", "running") or "running")
            status = "Paused" if outcome == "paused" else "Live"
            self.session_status_pill.setText(status)
            if self.focus_session_service is not None:
                self.current_timer.setText(
                    self._elapsed_text_seconds(self.focus_session_service.elapsed_seconds(live_session))
                )
            else:
                self.current_timer.setText(self._elapsed_text(live_session.started_at))
        else:
            self.current_timer.setText("00:00")

        if next_task is None:
            self.session_status_pill.setText("Quiet day" if live_session is None else "Live")
            self.current_task_title.setText("No active task")
            self.current_task_subtitle.setText("No scheduled work is blocking the day right now.")
        else:
            status = str(getattr(next_task, "status", "todo") or "todo").lower()
            status_label = "Doing" if status in {"doing", "in_progress"} else "Ready"
            if live_session is not None:
                status_label = (
                    "Paused"
                    if str(getattr(live_session, "outcome", "running")) == "paused"
                    else "Live"
                )
            self.session_status_pill.setText(status_label)
            self.current_task_title.setText(str(getattr(next_task, "title", "Untitled task")))
            self.current_task_subtitle.setText(
                str(getattr(next_task, "description", "") or self._task_time_text(next_task))
            )
            if live_session is None:
                estimated = int(getattr(next_task, "estimated_minutes", 30) or 30)
                self.current_timer.setText(f"{estimated:02d}:00")

        self._update_progress_fill(task_progress)
        self._rebuild_up_next(today_tasks, tasks)
        self._rebuild_signals(tasks, today_tasks)
        self._rebuild_search_labels()
        self._update_session_controls(live_session)

    def _update_progress_fill(self, progress: int) -> None:
        width = max(60, int(self.session_card.width() * max(0.12, progress / 100.0))) if self.session_card.width() > 0 else 140
        self.progress_fill.setFixedWidth(width)

    def _clear_rows(self, layout, rows: list[QFrame]) -> None:
        for row in rows:
            layout.removeWidget(row)
            row.deleteLater()
        rows.clear()

    def _rebuild_up_next(self, today_tasks: list[Any], all_tasks: list[Any]) -> None:
        self._clear_rows(self.up_next_layout, self._today_rows)
        source = [task for task in today_tasks if getattr(task, "status", "todo") != "done"] or [task for task in all_tasks if getattr(task, "status", "todo") != "done"]
        if not source:
            row = self._make_list_row("No scheduled work", "Inbox is clear for today.", "Free", "good")
            self.up_next_layout.addWidget(row)
            self._today_rows.append(row)
            return
        for task in source[:3]:
            status = str(getattr(task, "status", "todo") or "todo").lower()
            badge_text = {"done": "Done", "doing": "Doing", "in_progress": "Doing", "blocked": "Blocked"}.get(status, "Todo")
            tone = "good" if status == "done" else "accent" if status in {"doing", "in_progress"} else "danger" if status == "blocked" else "default"
            row = self._make_list_row(str(getattr(task, "title", "Untitled task")), self._task_time_text(task), badge_text, tone)
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            self.up_next_layout.addWidget(row)
            self._today_rows.append(row)

    def _rebuild_signals(self, all_tasks: list[Any], today_tasks: list[Any]) -> None:
        self._clear_rows(self.signal_grid, self._signal_rows)
        overdue = 0
        unscheduled = 0
        active = 0
        for task in all_tasks:
            due_at = getattr(task, "due_at", None)
            scheduled_start = getattr(task, "scheduled_start", None)
            status = str(getattr(task, "status", "todo") or "todo").lower()
            if status != "done":
                active += 1
            if due_at is not None and due_at.date() < date.today() and status != "done":
                overdue += 1
            if scheduled_start is None and status != "done":
                unscheduled += 1

        signals = [
            ("ACTIVE", str(active), f"{len(today_tasks)} tasks visible today.", "default"),
            ("UNSCHEDULED", str(unscheduled), "Needs a time block.", "danger" if unscheduled else "good"),
            ("OVERDUE", str(overdue), "Still unfinished.", "danger" if overdue else "good"),
        ]

        for label, value, subtitle, tone in signals:
            card, _, _ = make_kpi_tile(label, value, subtitle, tone=tone)
            self.signal_grid.addWidget(card, 1)
            self._signal_rows.append(card)

    def _make_list_row(self, title: str, subtitle: str, badge_text: str, tone: str = "default") -> QFrame:
        row = QFrame()
        row.setObjectName("ListRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(14, 12, 14, 12)
        row_layout.setSpacing(12)
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(4)
        item_lbl = make_label(title, "cardTitle")
        meta_lbl = make_label(subtitle, "muted", True)
        text_col.addWidget(item_lbl)
        text_col.addWidget(meta_lbl)
        row_layout.addLayout(text_col, 1)
        row_layout.addWidget(make_pill(badge_text, tone))
        return row

    def _pick_current_task(self, today_tasks: list[Any], all_tasks: list[Any], live_session: Any | None) -> Any | None:
        if live_session is not None:
            planned_id = getattr(live_session, "planned_task_id", None)
            if planned_id:
                for task in all_tasks:
                    if getattr(task, "id", None) == planned_id:
                        return task
        for collection in (today_tasks, all_tasks):
            for task in collection:
                if str(getattr(task, "status", "todo") or "todo").lower() in {"doing", "in_progress"}:
                    return task
            for task in collection:
                if str(getattr(task, "status", "todo") or "todo").lower() != "done":
                    return task
        return None

    def _task_time_text(self, task: Any) -> str:
        scheduled_start = getattr(task, "scheduled_start", None)
        scheduled_end = getattr(task, "scheduled_end", None)
        estimated_minutes = int(getattr(task, "estimated_minutes", 30) or 30)
        if scheduled_start and scheduled_end:
            mins = int((scheduled_end - scheduled_start).total_seconds() // 60)
            return f"{scheduled_start.strftime('%H:%M')} · {mins}m"
        if scheduled_start:
            return f"{scheduled_start.strftime('%H:%M')} · {estimated_minutes}m"
        due_at = getattr(task, "due_at", None)
        if due_at:
            return f"Due {due_at.strftime('%H:%M')} · {estimated_minutes}m"
        return f"Unscheduled · {estimated_minutes}m"

    def _compute_streak(self, tasks: list[Any]) -> int:
        completed_dates = sorted({getattr(task, "completed_at").date() for task in tasks if getattr(task, "completed_at", None) is not None}, reverse=True)
        streak = 0
        day = date.today()
        completed_set = set(completed_dates)
        while day in completed_set:
            streak += 1
            day = date.fromordinal(day.toordinal() - 1)
        return streak

    def _strongest_hour_text(self) -> str:
        sessions = self._recent_sessions(days=30)
        if sessions:
            buckets: dict[int, list[float]] = {}
            for session in sessions:
                started_at = getattr(session, "started_at", None)
                score = getattr(session, "focus_score_avg", None)
                if started_at is None or score is None:
                    continue
                buckets.setdefault(started_at.hour, []).append(float(score))
            if buckets:
                best_hour = max(buckets.items(), key=lambda item: sum(item[1]) / max(1, len(item[1])))[0]
                return f"{best_hour:02d}:00"
        return "18:00"

    def _recent_sessions(self, days: int = 7) -> list[Any]:
        if not hasattr(self.repository, "all_sessions"):
            return []
        try:
            sessions = list(self.repository.all_sessions())
        except Exception:
            return []
        cutoff = datetime.now() - timedelta(days=days)
        return [s for s in sessions if getattr(s, "started_at", None) and getattr(s, "started_at") >= cutoff]

    def _latest_live_session(self, sessions: list[Any]) -> Any | None:
        for session in sessions:
            if str(getattr(session, "outcome", "")) in {"running", "paused"}:
                return session
        return None

    def _focus_score_from_sessions(self, sessions: list[Any], fallback: int) -> int:
        scored = [float(s.focus_score_avg) for s in sessions if getattr(s, "focus_score_avg", None) is not None]
        if not scored:
            return fallback
        avg = sum(scored) / max(1, len(scored))
        return int(round(avg * 100)) if avg <= 1 else int(round(avg))

    def _elapsed_text(self, started_at: datetime | None) -> str:
        if started_at is None:
            return "00:00"
        delta = max(timedelta(0), datetime.now() - started_at)
        minutes = int(delta.total_seconds() // 60)
        seconds = int(delta.total_seconds() % 60)
        return f"{minutes:02d}:{seconds:02d}"

    def _elapsed_text_seconds(self, seconds: int) -> str:
        minutes = max(0, int(seconds // 60))
        seconds = max(0, int(seconds % 60))
        return f"{minutes:02d}:{seconds:02d}"

    def _update_session_clock(self) -> None:
        if self.focus_session_service is None:
            return
        session = self.focus_session_service.active_session()
        if session is None:
            if self.current_timer.text() != "00:00":
                self.current_timer.setText("00:00")
            return
        elapsed = self.focus_session_service.elapsed_seconds(session)
        target = getattr(session, "target_minutes", None) or self._target_minutes
        if target:
            remaining_seconds = max(0, target * 60 - elapsed)
            mm = remaining_seconds // 60
            ss = remaining_seconds % 60
            self.current_timer.setText(f"-{mm:02d}:{ss:02d}")
            # Warn at 5 minutes remaining (fire once)
            if remaining_seconds == 300:
                try:
                    from app.services.notification_service import NotificationService
                    if hasattr(self, "_notif_5min_fired") and self._notif_5min_fired == session.id:
                        pass
                    else:
                        self._notif_5min_fired = session.id  # type: ignore[attr-defined]
                except Exception:
                    pass
            # Auto-pause when target reached
            if remaining_seconds == 0:
                session_id_key = getattr(session, "id", "")
                already_fired = getattr(self, "_target_reached_session", None)
                if already_fired != session_id_key:
                    self._target_reached_session = session_id_key  # type: ignore[attr-defined]
                    try:
                        self.focus_session_service.pause_session()
                    except Exception:
                        pass
        else:
            self.current_timer.setText(self._elapsed_text_seconds(elapsed))

    def _selected_target_minutes(self) -> int | None:
        """Read the duration picker selection; returns None for open-ended."""
        try:
            value = self.duration_picker.currentData()
            return int(value) if value else None
        except Exception:
            return None

    def _update_session_controls(self, session: Any | None) -> None:
        if self.focus_session_service is None:
            for btn in [
                self.session_start_btn,
                self.session_pick_btn,
                self.session_plan_btn,
                self.session_pause_btn,
                self.session_resume_btn,
                self.session_stop_btn,
                self.session_complete_btn,
            ]:
                btn.setEnabled(False)
            return

        if session is None:
            self.session_start_btn.setEnabled(True)
            self.session_pick_btn.setEnabled(True)
            self.session_plan_btn.setEnabled(True)
            self.session_pause_btn.setEnabled(False)
            self.session_resume_btn.setEnabled(False)
            self.session_stop_btn.setEnabled(False)
            self.session_complete_btn.setEnabled(False)
            return

        outcome = str(getattr(session, "outcome", "running") or "running")
        is_running = outcome == "running"
        is_paused = outcome == "paused"

        self.session_start_btn.setEnabled(False)
        self.session_pick_btn.setEnabled(False)
        self.session_plan_btn.setEnabled(False)
        self.session_pause_btn.setEnabled(is_running)
        self.session_resume_btn.setEnabled(is_paused)
        self.session_stop_btn.setEnabled(True)
        self.session_complete_btn.setEnabled(True)

    def _start_focus_session(self) -> None:
        if self.focus_session_service is None:
            return
        self._target_minutes = self._selected_target_minutes()
        tasks = self.task_service.list_tasks()
        today_tasks = self.task_service.tasks_for_day(date.today())
        next_task = self._pick_current_task(today_tasks, tasks, None)
        task_id = getattr(next_task, "id", None) if next_task is not None else None
        session = self.focus_session_service.start_session(planned_task_id=task_id, source="next_task")
        if session is not None and self._target_minutes:
            try:
                session.target_minutes = self._target_minutes  # type: ignore[attr-defined]
            except Exception:
                pass
        self.refresh_data()

    def _start_focus_session_with_picker(self) -> None:
        if self.focus_session_service is None:
            return
        task_id = self._prompt_task_pick()
        if not task_id:
            return
        self.focus_session_service.start_session(planned_task_id=task_id, source="manual_pick")
        self.refresh_data()

    def _start_focus_session_with_planner(self) -> None:
        if self.focus_session_service is None:
            return
        task_id = self._pick_planner_task_id()
        if not task_id:
            return
        self.focus_session_service.start_session(planned_task_id=task_id, source="planner")
        self.refresh_data()

    def _pause_focus_session(self) -> None:
        if self.focus_session_service is None:
            return
        self.focus_session_service.pause_session()
        self.refresh_data()

    def _resume_focus_session(self) -> None:
        if self.focus_session_service is None:
            return
        self.focus_session_service.resume_session()
        self.refresh_data()

    def _stop_focus_session(self) -> None:
        if self.focus_session_service is None:
            return
        self.focus_session_service.stop_session(outcome="stopped")
        self.refresh_data()

    def _complete_focus_session(self) -> None:
        if self.focus_session_service is None:
            return
        self.focus_session_service.complete_session(mark_task_done=True, task_service=self.task_service)
        self.refresh_data()

    def _prompt_task_pick(self) -> str | None:
        tasks = [task for task in self.task_service.list_tasks() if getattr(task, "status", "todo") not in {"done", "cancelled"}]
        if not tasks:
            return None

        dialog = QDialog(self)
        dialog.setWindowTitle("Pick focus task")
        layout = QVBoxLayout(dialog)
        list_widget = QListWidget()
        for task in tasks:
            label = f"{task.title} · p{task.priority} · {task.estimated_minutes}m"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, task.id)
            list_widget.addItem(item)
        if list_widget.count() > 0:
            list_widget.setCurrentRow(0)
        layout.addWidget(list_widget)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        current = list_widget.currentItem()
        return current.data(Qt.ItemDataRole.UserRole) if current is not None else None

    def _pick_planner_task_id(self) -> str | None:
        result = self.planner_service.build_plan(anchor_day=date.today(), day_count=2)
        if not result.suggestions:
            return None
        return result.suggestions[0].task_id

    def _open_planner(self) -> None:
        window = self.window()
        if window is None:
            return
        target = cast(Any, window)
        if hasattr(target, "_navigate_to_key"):
            target._navigate_to_key("planner")

    def _rebuild_search_labels(self) -> None:
        labels: list[Any] = [label for label in self._static_labels if isValid(label)]
        dynamic_labels: list[QLabel] = []
        for row in [*self._today_rows, *self._signal_rows]:
            for label in row.findChildren(QLabel):
                if isValid(label):
                    dynamic_labels.append(label)
        labels.extend(dynamic_labels)
        labels.extend([self.hero_eyebrow, self.hero_title, self.hero_subtitle, self.current_task_title, self.current_task_subtitle])
        self.labels_to_search = [label for label in labels if isValid(label)]

    def filter_content(self, text: str):
        text = text.lower().strip()
        self.labels_to_search = [lbl for lbl in self.labels_to_search if isValid(lbl)]
        if not text:
            for lbl in self.labels_to_search:
                lbl.setProperty("highlighted", False)
                lbl.style().unpolish(lbl)
                lbl.style().polish(lbl)
            return
        for lbl in self.labels_to_search:
            if text in lbl.text().lower():
                lbl.setProperty("highlighted", True)
            else:
                lbl.setProperty("highlighted", False)
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)
    def set_live_focus_snapshot(self, snapshot: LiveFocusSnapshot | None) -> None:
        self._live_focus_snapshot = snapshot
        if snapshot is None:
            return

        score_pct = max(0, min(100, int(round(snapshot.focus_score * 100))))
        self.focus_ring.setValue(score_pct)
        self.kpi_focus_value.setText(str(score_pct))

        if snapshot.raw_drift_risk >= 0.55:
            self.kpi_focus_delta.setText("Drift elevated")
        elif snapshot.raw_drift_risk >= 0.30:
            self.kpi_focus_delta.setText("Minor drift")
        else:
            self.kpi_focus_delta.setText("Stable")

        self.hero_subtitle.setText(
            f"Focus score reflects the last hour of activity. Live drift risk: {int(round(snapshot.raw_drift_risk * 100))}%."
        )

    def on_live_focus_updated(self, snapshot: object) -> None:
        """Public entry point called by MainWindow to push live focus data."""
        self._apply_live_snapshot(snapshot)

    def _on_live_focus_tick(self, snapshot: object) -> None:
        """Direct connection from FocusTickEngine.focus_updated signal."""
        self._apply_live_snapshot(snapshot)

    def _apply_live_snapshot(self, snapshot: object) -> None:
        """
        Updates only the live-driven widgets from a FocusRuntimeSnapshot.
        Does NOT call refresh_data() — that is repo-triggered and heavier.
        """
        from app.services.focus_tick_engine import FocusRuntimeSnapshot
        if not isinstance(snapshot, FocusRuntimeSnapshot):
            return

        # Focus ring: convert 0.0–1.0 to 0–100
        focus_int = max(0, min(100, int(round(snapshot.focus_score * 100))))
        self.focus_ring.setValue(focus_int)

        # Live KPI tile
        self.kpi_focus_value.setText(str(focus_int))

        # Drift risk tone on the focus KPI delta label
        drift = snapshot.raw_drift_risk
        if drift < 0.35:
            delta_text = "Focused"
            pill_type = "PillGood"
        elif drift < 0.65:
            delta_text = "Drifting"
            pill_type = "Pill"
        else:
            delta_text = "Distracted"
            pill_type = "PillDanger"

        self.kpi_focus_delta.setText(delta_text)
        self.kpi_focus_delta.setObjectName(pill_type)
        self.kpi_focus_delta.style().unpolish(self.kpi_focus_delta)
        self.kpi_focus_delta.style().polish(self.kpi_focus_delta)

        # Feature attribution tooltip on the ring
        explanation = self._explain_focus_score(snapshot)
        if explanation:
            self.focus_ring.setToolTip(explanation)
            self.kpi_focus_value.setToolTip(explanation)

        # Model load status badge (optional, only logs when it changes)
        if not snapshot.model_loaded:
            self.kpi_focus_delta.setText("No model")


    def _dynamic_greeting(self) -> str:
        """Returns a time-of-day contextual greeting, personalised when profile name is available."""
        hour = datetime.now().hour
        name: str = ""
        try:
            if hasattr(self.repository, "get_profile"):
                profile = self.repository.get_profile()
                n = getattr(profile, "display_name", "") or ""
                if n and n.lower() not in {"abte user", "user", ""}:
                    name = f", {n.split()[0]}"
        except Exception:
            pass

        if 5 <= hour < 12:
            return f"Good morning{name} \u2014 ready to make progress?"
        elif 12 <= hour < 17:
            return f"Good afternoon{name} \u2014 time to get things done."
        elif 17 <= hour < 21:
            return f"Good evening{name} \u2014 ready for a focused block?"
        else:
            return f"Working late{name}? Let's keep it light and focused."

    def _explain_focus_score(self, snapshot: object) -> str:
        """Build a 'Why is my score X?' tooltip string from feature attributions."""
        try:
            if self.focus_tick_engine is None:
                return ""
            model = getattr(self.focus_tick_engine, "_model", None)
            if model is None or not hasattr(model, "explain_prediction"):
                return ""
            extractor = getattr(self.focus_tick_engine, "_extractor", None)
            last_obs = getattr(self.focus_tick_engine, "_last_obs", None)
            if extractor is None or last_obs is None:
                return ""
            features = extractor.build_features(last_obs)
            contributions = model.explain_prediction(features, top_n=3)
            if not contributions:
                return ""
            lines = ["\u2139 Why this score?"]
            for label, pct in contributions:
                lines.append(f"  \u2022 {label} ({pct:.0f}%)")
            return "\n".join(lines)
        except Exception:
            return ""