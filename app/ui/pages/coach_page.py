from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from PySide6.QtCore import Qt, Signal, QThread, QTimer
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QPlainTextEdit, QVBoxLayout, QWidget, QLabel

from app.services.handle_tasks import TaskService
from app.ui.ui_helpers import make_button, make_card, make_label, make_pill


class CoachPage(QWidget):
    tasksCreated = Signal(list)
    decomposeRequested = Signal(str)

    def __init__(self, metrics: Any, task_service: TaskService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.metrics = metrics
        self.task_service = task_service
        self._slm_service: Any = None
        self._draft_tasks: list[Any] = []
        self._draft_thread: QThread | None = None
        self._review_thread: QThread | None = None

        self._spinner_timer = QTimer(self)
        self._spinner_frames = ["|", "/", "-", "\\"]
        self._spinner_index = 0
        self._spinner_timer.timeout.connect(self._advance_spinner)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        summary_card, summary_layout = make_card(
            "Weekly Coach",
            "Review open work, surface friction, and turn broad goals into concrete next steps.",
            elevated=True,
        )
        self.summary_label = make_label("", "muted", word_wrap=True)
        self.generate_review_btn = make_button("Generate review", "secondary")
        self.generate_review_btn.setToolTip("Ask the AI coach to summarise this week's focus and tasks")
        self.generate_review_btn.clicked.connect(self._on_generate_review_clicked)

        self.review_spinner = make_label("", "muted")
        self.review_spinner.setFixedWidth(16)

        summary_actions = QHBoxLayout()
        summary_actions.setContentsMargins(0, 0, 0, 0)
        summary_actions.addWidget(self.generate_review_btn)
        summary_actions.addWidget(self.review_spinner)
        summary_actions.addStretch(1)
        summary_layout.addWidget(self.summary_label)
        summary_layout.addLayout(summary_actions)
        root.addWidget(summary_card)

        row = QHBoxLayout()
        row.setSpacing(14)

        left_card, left_layout = make_card(
            "Weekly review",
            "A calm overview of blocked, unscheduled, and high-priority work.",
        )
        self.review_list = QListWidget()
        left_layout.addWidget(self.review_list)
        row.addWidget(left_card, 1)

        right_card, right_layout = make_card(
            "Chat to draft tasks",
            "Describe work naturally. Review drafts before adding them.",
        )
        self.model_status = make_pill("Model: unknown", "default")
        right_layout.addWidget(self.model_status)
        self.chat_input = QPlainTextEdit()
        self.chat_input.setPlaceholderText(
            "Example: next week I need to prepare taxes, email the contractor, and outline the onboarding document"
        )
        self.chat_input.setMinimumHeight(140)

        actions = QHBoxLayout()
        self.draft_btn = make_button("Draft tasks", "secondary")
        self.create_btn = make_button("Create drafted tasks", "primary")
        self.draft_spinner = make_label("", "muted")
        self.draft_spinner.setFixedWidth(16)

        actions.addWidget(self.draft_btn)
        actions.addWidget(self.create_btn)
        actions.addWidget(self.draft_spinner)
        actions.addStretch(1)

        self.draft_list = QListWidget()
        right_layout.addWidget(self.chat_input)
        right_layout.addLayout(actions)
        right_layout.addWidget(self.draft_list)
        row.addWidget(right_card, 1)

        root.addLayout(row, 1)

        self.draft_btn.clicked.connect(self._draft_tasks_from_chat)
        self.create_btn.clicked.connect(self._persist_drafts)

    def set_slm_service(self, slm_service: Any) -> None:
        """Wire the SLM service for on-demand weekly review generation (#31)."""
        self._slm_service = slm_service

        self.refresh_data()
        self._refresh_model_status()

    def refresh_data(self) -> None:
        snapshot = self.task_service.weekly_coach_snapshot()

        # Try to load the most recently saved weekly review report
        latest_report = None
        if hasattr(self.task_service.repository, "get_latest_coach_report"):
            try:
                latest_report = self.task_service.repository.get_latest_coach_report()
            except Exception:
                pass

        if latest_report and latest_report.get("summary_text"):
            self.summary_label.setText(latest_report["summary_text"])
        else:
            self.summary_label.setText(
                f"Active: {snapshot['active_count']} · "
                f"Completed: {snapshot['completed_count']} · "
                f"Blocked: {snapshot['blocked_count']} · "
                f"Unscheduled: {snapshot['unscheduled_count']} · "
                f"High priority: {snapshot['high_priority_count']}"
            )

        self.review_list.clear()

        # If a saved coach report is found, prepend it to the review list
        if latest_report and latest_report.get("summary_text"):
            header = QListWidgetItem("— AI Coach Review —")
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            self.review_list.addItem(header)
            review_text = latest_report["summary_text"]
            review_item = QListWidgetItem(review_text[:500] + ("…" if len(review_text) > 500 else ""))
            review_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.review_list.addItem(review_item)

        for label, tasks in [
            ("High priority", snapshot["high_priority_tasks"]),
            ("Blocked or missed", snapshot["blocked_tasks"]),
            ("Unscheduled", snapshot["unscheduled_tasks"]),
        ]:
            if not tasks:
                continue
            header = QListWidgetItem(label)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            self.review_list.addItem(header)
            for task in tasks:
                item = QListWidgetItem(f"• {task.title} ({task.estimated_minutes}m)")
                item.setData(Qt.ItemDataRole.UserRole, task.id)
                self.review_list.addItem(item)

    def _draft_tasks_from_chat(self) -> None:
        text = self.chat_input.toPlainText().strip()
        self.draft_list.clear()
        self._draft_tasks = []

        if not text:
            self.draft_list.addItem("Enter a description first.")
            return

        self.draft_btn.setEnabled(False)
        self._set_model_status("Processing", tone="accent")
        self._run_draft(text)

    def _persist_drafts(self) -> None:
        if not self._draft_tasks:
            return

        created_ids: list[str] = []
        for draft in self._draft_tasks:
            created = self.task_service.create_task(
                title=draft.title,
                description=draft.description,
                estimated_minutes=draft.estimated_minutes,
                due_at=draft.due_at,
                priority=draft.priority,
                tags=list(draft.tags),
                status=draft.status,
                scheduled_start=draft.scheduled_start,
                scheduled_end=draft.scheduled_end,
                source=draft.source,
                energy_cost=draft.energy_cost,
                focus_score_hint=draft.focus_score_hint,
                recurrence_rule=draft.recurrence_rule,
                parent_task_id=draft.parent_task_id,
                meta=dict(draft.meta),
            )
            created_ids.append(created.id)

        self.tasksCreated.emit(created_ids)
        self._draft_tasks = []
        self.chat_input.clear()
        self.draft_list.clear()
        self.refresh_data()

    def _refresh_model_status(self) -> None:
        slm = getattr(self.task_service, "_slm_service", None)
        if slm is None or not hasattr(slm, "current_config"):
            self._set_model_status("Model: unavailable", tone="default")
            return
        cfg = slm.current_config()
        if cfg is None:
            self._set_model_status("Model: not configured", tone="default")
            return
        if hasattr(slm, "is_model_ready") and slm.is_model_ready():
            self._set_model_status("Model: ready", tone="good")
            return
        self._set_model_status("Model: missing", tone="danger")

    def _set_model_status(self, text: str, *, tone: str = "default") -> None:
        self.model_status.setText(text)
        object_name = {
            "default": "Pill",
            "accent": "PillAccent",
            "danger": "PillDanger",
            "good": "PillGood",
        }.get(tone, "Pill")
        self.model_status.setObjectName(object_name)
        self.model_status.style().unpolish(self.model_status)
        self.model_status.style().polish(self.model_status)

    def _run_draft(self, text: str) -> None:
        if self._draft_thread is not None:
            return

        class _DraftWorker(QThread):
            done = Signal(list)
            failed = Signal(str)

            def __init__(self, service: TaskService, content: str) -> None:
                super().__init__()
                self._service = service
                self._text = content

            def run(self) -> None:
                try:
                    drafts = self._service.create_tasks_from_natural_language(self._text, persist=False)
                except Exception as exc:
                    self.failed.emit(str(exc))
                    return
                self.done.emit(drafts)

        worker = _DraftWorker(self.task_service, text)
        worker.done.connect(self._on_draft_done)
        worker.failed.connect(self._on_draft_failed)
        worker.finished.connect(self._on_draft_finished)
        self._draft_thread = worker
        self._start_spinner()
        worker.start()

    def _on_draft_done(self, drafts: list) -> None:
        self._draft_tasks = drafts
        if not drafts:
            self.draft_list.addItem("No actionable task drafts were returned.")
            return
        for task in drafts:
            self.draft_list.addItem(
                f"{task.title} · {task.estimated_minutes}m · priority {task.priority}"
            )

    def _on_draft_failed(self, message: str) -> None:
        self.draft_list.addItem(f"Drafting failed: {message}")

    def _on_draft_finished(self) -> None:
        self._draft_thread = None
        self.draft_btn.setEnabled(True)
        self._stop_spinner()
        self._refresh_model_status()

    def _on_generate_review_clicked(self) -> None:
        """Start an on-demand weekly review generation in a background thread (#31)."""
        if self._review_thread is not None:
            return  # Already running
        if self._slm_service is None:
            self.summary_label.setText("SLM service not available. Configure a model in Settings.")
            return

        self.generate_review_btn.setEnabled(False)
        self.summary_label.setText("Generating review\u2026")

        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        week_end = today
        slm = self._slm_service

        class _ReviewWorker(QThread):
            done = Signal(str)
            failed = Signal(str)

            def run(self) -> None:
                try:
                    if hasattr(slm, "generate_weekly_review"):
                        text = slm.generate_weekly_review(
                            week_start=week_start,
                            week_end=week_end,
                        )
                    else:
                        text = "Review generation is not supported by the current model backend."
                    self.done.emit(str(text) if text else "No review generated.")
                except Exception as exc:
                    self.failed.emit(str(exc))

        worker = _ReviewWorker()
        worker.done.connect(self._on_review_done)
        worker.failed.connect(self._on_review_failed)
        worker.finished.connect(self._on_review_finished)
        self._review_thread = worker
        self._start_spinner()
        worker.start()

    def _on_review_done(self, text: str) -> None:
        self.summary_label.setText(text)
        # Also display in a list item so it's visible in the review card
        header = QListWidgetItem("\u2500 AI Coach Review \u2500")
        header.setFlags(Qt.ItemFlag.NoItemFlags)
        self.review_list.insertItem(0, header)
        review_item = QListWidgetItem(text[:500] + ("\u2026" if len(text) > 500 else ""))
        review_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.review_list.insertItem(1, review_item)

    def _on_review_failed(self, message: str) -> None:
        self.summary_label.setText(f"Review failed: {message}")

    def _on_review_finished(self) -> None:
        self._review_thread = None
        self.generate_review_btn.setEnabled(True)
        self._stop_spinner()

    def _advance_spinner(self) -> None:
        frame = self._spinner_frames[self._spinner_index]
        self.review_spinner.setText(frame if self._review_thread else "")
        self.draft_spinner.setText(frame if self._draft_thread else "")
        self._spinner_index = (self._spinner_index + 1) % len(self._spinner_frames)

    def _start_spinner(self) -> None:
        if not self._spinner_timer.isActive():
            self._spinner_index = 0
            self._spinner_timer.start(120)

    def _stop_spinner(self) -> None:
        if not self._review_thread and not self._draft_thread:
            self._spinner_timer.stop()
            self.review_spinner.setText("")
            self.draft_spinner.setText("")