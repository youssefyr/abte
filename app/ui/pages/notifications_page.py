from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget

from app.ui.pages.base_page import BasePage
from app.ui.ui_helpers import make_card, make_label, make_pill


class NotificationsPage(BasePage):
    def __init__(self, metrics, notification_service: Any, parent=None):
        super().__init__(metrics, parent)
        self.notification_service = notification_service
        self._notification_ids: list[str] = []
        self.labels_to_search = []

        stats_card, stats_layout = make_card("Notifications", "Review recent events and clear the queue.", elevated=True)
        pills = QHBoxLayout()
        self.total_pill = make_pill("0 total", "default")
        self.unread_pill = make_pill("0 unread", "accent")
        self.warning_pill = make_pill("0 warning", "default")
        self.error_pill = make_pill("0 error", "default")
        for pill in [self.total_pill, self.unread_pill, self.warning_pill, self.error_pill]:
            pills.addWidget(pill)
        pills.addStretch()
        stats_layout.addLayout(pills)
        self.main_layout.addWidget(stats_card)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)

        list_card, list_layout = make_card("Feed", "Newest items first.", elevated=False)
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("NotificationsList")
        list_layout.addWidget(self.list_widget)

        actions = QHBoxLayout()
        self.mark_read_btn = QPushButton("Mark selected read")
        self.mark_all_btn = QPushButton("Mark all read")
        self.delete_btn = QPushButton("Dismiss selected")
        actions.addWidget(self.mark_read_btn)
        actions.addWidget(self.mark_all_btn)
        actions.addWidget(self.delete_btn)
        actions.addStretch()
        list_layout.addLayout(actions)

        self.detail_card, detail_layout = make_card("Details", "Event context and action routing.", elevated=False)
        self.detail_title = make_label("No notification selected", "cardTitle")
        self.detail_body = make_label("Select an item to inspect its message and metadata.", "muted", True)
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_body)

        body_layout.addWidget(list_card, 2)
        body_layout.addWidget(self.detail_card, 1)
        self.main_layout.addWidget(body, 1)

        self.list_widget.currentRowChanged.connect(self._on_row_changed)
        self.mark_read_btn.clicked.connect(self._mark_selected_read)
        self.mark_all_btn.clicked.connect(self._mark_all_read)
        self.delete_btn.clicked.connect(self._delete_selected)

        repo = getattr(self.notification_service, "repository", None)
        if repo is not None and hasattr(repo, "notifications_changed"):
            repo.notifications_changed.connect(self.refresh_data)

        self.refresh_data()

    def refresh_data(self) -> None:
        summary = self.notification_service.summary(hours=24)
        items = self.notification_service.list_notifications(limit=300)
        selected_id = self._notification_ids[self.list_widget.currentRow()] if 0 <= self.list_widget.currentRow() < len(self._notification_ids) else None

        self.total_pill.setText(f"{summary.total} total")
        self.unread_pill.setText(f"{summary.unread} unread")
        self.warning_pill.setText(f"{summary.warning} warning")
        self.error_pill.setText(f"{summary.error} error")

        self.list_widget.clear()
        self._notification_ids.clear()

        for item in items:
            stamp = item.created_at.strftime("%Y-%m-%d %H:%M") if item.created_at else "unknown time"
            state = "unread" if item.read_at is None else "read"
            row = QListWidgetItem(f"[{item.level.upper()}] {item.title}\n{item.message}\n{stamp} · {state}")
            self.list_widget.addItem(row)
            self._notification_ids.append(item.id)

        if not self._notification_ids:
            self.detail_title.setText("No notification selected")
            self.detail_body.setText("No notifications available.")
            return

        if selected_id in self._notification_ids:
            self.list_widget.setCurrentRow(self._notification_ids.index(selected_id))
        else:
            self.list_widget.setCurrentRow(0)

    def filter_content(self, text: str) -> None:
        text = text.strip().lower()
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            item.setHidden(bool(text) and text not in item.text().lower())

    def _on_row_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._notification_ids):
            self.detail_title.setText("No notification selected")
            self.detail_body.setText("Select a notification to inspect it.")
            return
        notification = self.notification_service.repository.notification_by_id(self._notification_ids[row])
        if notification is None:
            return
        self.detail_title.setText(notification.title)
        extra = []
        if notification.action_key:
            extra.append(f"action={notification.action_key}")
        if notification.meta:
            extra.append(f"meta={notification.meta}")
        details = [notification.message, f"Level: {notification.level}"]
        if notification.created_at:
            details.append(f"Created: {notification.created_at.strftime('%Y-%m-%d %H:%M')}")
        if notification.read_at:
            details.append(f"Read: {notification.read_at.strftime('%Y-%m-%d %H:%M')}")
        details.extend(extra)
        self.detail_body.setText("\n".join(details))

    def _mark_selected_read(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self._notification_ids):
            return
        self.notification_service.mark_read(self._notification_ids[row])

    def _mark_all_read(self) -> None:
        count = self.notification_service.mark_all_read()
        if count == 0:
            QMessageBox.information(self, "Notifications", "There were no unread notifications.")

    def _delete_selected(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self._notification_ids):
            return
        notification_id = self._notification_ids[row]
        notification = self.notification_service.repository.notification_by_id(notification_id)
        if notification is None:
            return
        answer = QMessageBox.question(self, "Dismiss notification", f"Dismiss '{notification.title}'?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.notification_service.dismiss(notification_id)
