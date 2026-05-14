from __future__ import annotations

from dataclasses import dataclass
import uuid
from datetime import datetime, timezone
from typing import Any

from app.data.entities import NotificationItem


@dataclass(slots=True)
class NotificationSummary:
    total: int
    unread: int
    info: int
    warning: int
    error: int


class NotificationService:
    def __init__(self, repository: Any) -> None:
        self.repository = repository
        self._suppressed = False
        self._queue: list[dict[str, Any]] = []
        self._on_publish_callbacks = []

    def add_publish_callback(self, cb: Any) -> None:
        self._on_publish_callbacks.append(cb)

    def set_suppressed(self, suppress: bool) -> None:
        self._suppressed = suppress
        if not suppress:
            self.flush()

    def flush(self) -> None:
        while self._queue:
            kwargs = self._queue.pop(0)
            self._publish_immediate(**kwargs)

    def publish(
        self,
        title: str,
        message: str,
        *,
        level: str = "info",
        action_key: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> NotificationItem:
        title = title.strip()
        message = message.strip()
        if not title:
            raise ValueError("Notification title is required")
        if not message:
            raise ValueError("Notification message is required")
        kwargs = {
            "title": title,
            "message": message,
            "level": level,
            "action_key": action_key,
            "meta": meta,
        }
        if self._suppressed and level != "error":
            self._queue.append(kwargs)
            # Create a placeholder dummy item since we aren't saving it yet
            return NotificationItem(
                id=f"notif-queued-{uuid.uuid4().hex[:8]}",
                title=title,
                message=message,
                level=level,
                created_at=datetime.now(timezone.utc),
                read_at=None,
                action_key=action_key,
                meta=meta or {},
            )
        return self._publish_immediate(**kwargs)

    def _publish_immediate(
        self,
        title: str,
        message: str,
        level: str = "info",
        action_key: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> NotificationItem:
        item = NotificationItem(
            id=f"notif-{uuid.uuid4().hex[:12]}",
            title=title,
            message=message,
            level=level,
            created_at=datetime.now(timezone.utc),
            read_at=None,
            action_key=action_key,
            meta=meta or {},
        )
        saved = self.repository.add_notification(item)
        for cb in self._on_publish_callbacks:
            try:
                cb(saved)
            except Exception:
                pass
        return saved

    def list_notifications(self, *, unread_only: bool = False, limit: int | None = None) -> list[NotificationItem]:
        return list(self.repository.all_notifications(unread_only=unread_only, limit=limit))

    def mark_read(self, notification_id: str) -> bool:
        return bool(self.repository.mark_notification_read(notification_id))

    def mark_all_read(self) -> int:
        return int(self.repository.mark_all_notifications_read())

    def dismiss(self, notification_id: str) -> bool:
        return bool(self.repository.delete_notification(notification_id))

    def summary(self, *, hours: int = 24) -> NotificationSummary:
        items = self.list_notifications(limit=500)
        unread = sum(1 for item in items if item.read_at is None)
        counts = self.repository.recent_notification_summary(hours)
        return NotificationSummary(
            total=len(items),
            unread=unread,
            info=int(counts.get("info", 0)),
            warning=int(counts.get("warning", 0)),
            error=int(counts.get("error", 0)),
        )
