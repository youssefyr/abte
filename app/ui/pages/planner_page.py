from __future__ import annotations

from datetime import date
from typing import Any
from shiboken6 import isValid


from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout

from app.services.planner_service import PlannerService
from app.ui.pages.base_page import BasePage
from app.ui.ui_helpers import make_button, make_card, make_label, make_pill


class PlannerPage(BasePage):
    def __init__(self, metrics, repository: Any, parent=None) -> None:
        super().__init__(metrics, parent)
        self.repository = repository
        self.planner_service = PlannerService(repository)
        self._last_result = None
        self.labels_to_search: list[Any] = []
        self._suggestion_rows: list[QFrame] = []
        self._energy_rows: list[QFrame] = []

        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(metrics.card_gap)

        top_row = QHBoxLayout()
        top_row.setSpacing(metrics.card_gap)

        self.overview_card, overview_layout = make_card(
            'Planner engine',
            'Constraint scheduling with energy-aware ranking and no overlap.',
            elevated=True,
        )
        self.status_pill = make_pill('Idle', 'default')
        overview_layout.addWidget(self.status_pill)
        self.summary_label = make_label('No plan generated yet.', 'muted', True)
        overview_layout.addWidget(self.summary_label)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.generate_btn = make_button('Generate plan', 'primary')
        self.apply_btn = make_button('Apply plan', 'secondary')
        self.refresh_btn = make_button('Refresh signals', 'ghost')
        actions.addWidget(self.generate_btn)
        actions.addWidget(self.apply_btn)
        actions.addWidget(self.refresh_btn)
        actions.addStretch(1)
        overview_layout.addLayout(actions)

        self.signal_card, self.signal_layout = make_card(
            'Planning signals',
            'Energy windows and scheduling pressure.',
            elevated=False,
        )
        top_row.addWidget(self.overview_card, 2)
        top_row.addWidget(self.signal_card, 1)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(metrics.card_gap)
        self.suggestions_card, self.suggestions_layout = make_card(
            'Suggestions',
            'Best-fit placements over the visible planning horizon.',
            elevated=False,
        )
        self.energy_card, self.energy_layout = make_card(
            'Energy profile',
            'Historical focus clusters collapsed into hourly windows.',
            elevated=False,
        )
        bottom_row.addWidget(self.suggestions_card, 2)
        bottom_row.addWidget(self.energy_card, 1)

        self.main_layout.addLayout(top_row)
        self.main_layout.addLayout(bottom_row)

        self.generate_btn.clicked.connect(self.generate_plan)
        self.apply_btn.clicked.connect(self.apply_plan)
        self.refresh_btn.clicked.connect(self.refresh_signals)

        if hasattr(self.repository, 'tasks_changed'):
            self.repository.tasks_changed.connect(self.refresh_signals)
        if hasattr(self.repository, 'calendar_changed'):
            self.repository.calendar_changed.connect(self.refresh_signals)
        if hasattr(self.repository, 'sessions_changed'):
            self.repository.sessions_changed.connect(self.refresh_signals)

        self.refresh_signals()

    def generate_plan(self) -> None:
        self._last_result = self.planner_service.build_plan(anchor_day=date.today(), day_count=5)
        suggestion_count = len(self._last_result.suggestions)
        unscheduled = len(self._last_result.unscheduled_task_ids)
        self.status_pill.setText('Plan ready' if suggestion_count else 'No fit')
        self.summary_label.setText(f'{suggestion_count} tasks placed, {unscheduled} left unscheduled.')
        self._rebuild_suggestions()
        self._rebuild_signals_from_result()

    def apply_plan(self) -> None:
        if self._last_result is None:
            self.summary_label.setText('Generate a plan before applying it.')
            return
        changed = self.planner_service.apply_plan(self._last_result)
        self.status_pill.setText('Applied' if changed else 'No changes')
        self.summary_label.setText(f'{changed} task placements were written to the repository.')
        self.refresh_signals()

    def refresh_signals(self) -> None:
        self._rebuild_energy_profile()
        self._rebuild_signal_rows()
        if self._last_result is not None:
            self._rebuild_suggestions()

    def _rebuild_signal_rows(self) -> None:
        self._clear_rows(self.signal_layout, [])
        tasks = list(self.repository.all_tasks()) if hasattr(self.repository, 'all_tasks') else []
        active = [task for task in tasks if getattr(task, 'status', 'todo') not in {'done', 'cancelled'}]
        unscheduled = [task for task in active if getattr(task, 'scheduled_start', None) is None]
        overdue = [task for task in active if getattr(task, 'due_at', None) is not None and task.due_at.date() < date.today()]
        items = [
            ('Active tasks', f'{len(active)} tasks are currently eligible for planning.'),
            ('Unscheduled tasks', f'{len(unscheduled)} active tasks still need a time block.'),
            ('Overdue tasks', f'{len(overdue)} unfinished tasks are already past due.'),
        ]
        for title, subtitle in items:
            row = QFrame()
            row.setObjectName('ListRow')
            layout = QVBoxLayout(row)
            layout.setContentsMargins(12, 12, 12, 12)
            title_lbl = make_label(title, 'cardTitle')
            subtitle_lbl = make_label(subtitle, 'muted', True)
            self.labels_to_search.extend([title_lbl, subtitle_lbl])
            layout.addWidget(title_lbl)
            layout.addWidget(subtitle_lbl)
            self.signal_layout.addWidget(row)
        self.signal_layout.addStretch(1)

    def _rebuild_signals_from_result(self) -> None:
        if self._last_result is None:
            return
        self._rebuild_signal_rows()
        diag = self._last_result.diagnostics
        diag_row = QFrame()
        diag_row.setObjectName('ListRow')
        layout = QVBoxLayout(diag_row)
        layout.setContentsMargins(12, 12, 12, 12)
        title_lbl = make_label('Solver diagnostics', 'cardTitle')
        subtitle_lbl = make_label(
            f"{diag.get('solver', 'unknown')} · candidates {diag.get('candidate_count', 0)} · slots {diag.get('slot_count', 0)}",
            'muted',
            True,
        )
        layout.addWidget(title_lbl)
        layout.addWidget(subtitle_lbl)
        self.signal_layout.insertWidget(0, diag_row)

    def _rebuild_suggestions(self) -> None:
        while self.suggestions_layout.count():
            item = self.suggestions_layout.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if self._last_result is None or not self._last_result.suggestions:
            self.suggestions_layout.addWidget(self._make_row('No suggestions', 'Generate a plan to view solver output.', 'Idle'))
            self.suggestions_layout.addStretch(1)
            return
        for suggestion in self._last_result.suggestions:
            subtitle = (
                f"{suggestion.scheduled_start.strftime('%a %H:%M')} → {suggestion.scheduled_end.strftime('%H:%M')}"
                f" · energy {suggestion.energy_score:.2f} · {suggestion.reason}"
            )
            self.suggestions_layout.addWidget(self._make_row(suggestion.title, subtitle, f"{suggestion.score:.2f}"))
        if self._last_result.unscheduled_task_ids:
            self.suggestions_layout.addWidget(
                self._make_row(
                    'Unscheduled tasks',
                    f"{len(self._last_result.unscheduled_task_ids)} tasks had no feasible slot in the current horizon.",
                    'Review',
                )
            )
        self.suggestions_layout.addStretch(1)

    def _rebuild_energy_profile(self) -> None:
        while self.energy_layout.count():
            item = self.energy_layout.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        profile = self.planner_service.energy_profile()
        ordered = sorted(profile.items(), key=lambda item: item[1], reverse=True)[:6]
        if not ordered:
            self.energy_layout.addWidget(self._make_row('No focus history', 'Energy modeling will improve after session logs accumulate.', 'Base'))
            self.energy_layout.addStretch(1)
            return
        for hour, score in ordered:
            label = f'{hour:02d}:00'
            subtitle = 'Historically stronger focus cluster.' if score >= 0.7 else 'Moderate focus window.'
            self.energy_layout.addWidget(self._make_row(label, subtitle, f'{score:.2f}'))
        self.energy_layout.addStretch(1)

    def _make_row(self, title: str, subtitle: str, badge: str) -> QFrame:
        row = QFrame()
        row.setObjectName('ListRow')
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 10, 12, 10)
        text_col = QVBoxLayout()
        title_lbl = make_label(title)
        subtitle_lbl = make_label(subtitle, 'muted', True)
        self.labels_to_search.extend([title_lbl, subtitle_lbl])
        text_col.addWidget(title_lbl)
        text_col.addWidget(subtitle_lbl)
        layout.addLayout(text_col)
        layout.addStretch(1)
        layout.addWidget(make_pill(badge, 'default'))
        return row

    def _clear_rows(self, layout, rows) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def filter_content(self, text: str) -> None:
        text = text.lower().strip()
        if not text:
            for label in self.labels_to_search:
                if label is None or not isValid(label):
                    continue
                label.setStyleSheet("")
            return
        for label in self.labels_to_search:
            if label is None or not isValid(label):
                continue
            elif text in label.text().lower():
                label.setStyleSheet(f'color: {self.palette().color(QPalette.ColorRole.HighlightedText).name()}; background-color: {self.palette().color(QPalette.ColorRole.Highlight).name()}; border-radius: 4px;')
            else:
                label.setStyleSheet("")