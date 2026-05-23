# app/services/sidebar_template_service.py
from __future__ import annotations
from datetime import datetime
from typing import Any


class SidebarTemplateService:
    def __init__(
        self,
        repository: Any,
        settings: Any,
        plugin_manager: Any,
        gaze_service: Any = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.plugin_manager = plugin_manager
        self.gaze_service = gaze_service

    def render(self, template: str, username: str) -> str:
        if not template:
            return ""

        # 1. {{plugin_number}}
        plugin_count = 0
        if self.plugin_manager and hasattr(self.plugin_manager, "plugins"):
            try:
                plugins = self.plugin_manager.plugins()
                plugin_count = sum(1 for p in plugins if getattr(p, "id", "") != "core.demo")
            except Exception:
                pass

        # 2. {{username}}
        resolved = template.replace("{{username}}", username or "abte user")

        # 3. {{task_count}}, 4. {{todo_count}}, 5. {{done_count}}
        task_count = 0
        todo_count = 0
        done_count = 0
        if self.repository and hasattr(self.repository, "all_tasks"):
            try:
                tasks = self.repository.all_tasks()
                task_count = len(tasks)
                for t in tasks:
                    status = getattr(t, "status", "todo")
                    if status == "done":
                        done_count += 1
                    elif status in {"todo", "in_progress"}:
                        todo_count += 1
            except Exception:
                pass

        # 6. {{focus_session_count}}, 7. {{total_focus_minutes}}
        focus_session_count = 0
        total_focus_minutes = 0
        if self.repository and hasattr(self.repository, "all_sessions"):
            try:
                sessions = self.repository.all_sessions()
                completed_focus = [
                    s for s in sessions
                    if getattr(s, "mode", "focus") == "focus"
                    and getattr(s, "outcome", "completed") == "completed"
                ]
                focus_session_count = len(completed_focus)
                total_seconds = 0
                for s in completed_focus:
                    started = getattr(s, "started_at", None)
                    ended = getattr(s, "ended_at", None)
                    if started and ended:
                        total_seconds += (ended - started).total_seconds()
                total_focus_minutes = int(total_seconds // 60)
            except Exception:
                pass

        # 8. {{current_date}}, 9. {{current_time}}
        now = datetime.now()
        current_date = now.strftime("%Y-%m-%d")
        current_time = now.strftime("%H:%M")

        # 10. {{unread_notifications}}
        unread_notifications = 0
        if self.repository and hasattr(self.repository, "all_notifications"):
            try:
                unread_notifications = len(self.repository.all_notifications(unread_only=True))
            except Exception:
                pass

        # 11. {{gaze_status}}
        gaze_status = "inactive"
        if self.gaze_service and hasattr(self.gaze_service, "is_running"):
            try:
                gaze_status = "active" if self.gaze_service.is_running() else "inactive"
            except Exception:
                pass
        elif self.settings:
            try:
                gaze_status = "active" if self.settings.get("Vision/enable_gaze", False) else "inactive"
            except Exception:
                pass

        # 12. {{theme_name}}
        theme_name = "forest_focus"
        if self.settings:
            try:
                theme_name = str(self.settings.get("Settings/theme", "forest_focus"))
            except Exception:
                pass

        # Replace all placeholders in resolved template
        placeholders = {
            "{{plugin_number}}": str(plugin_count),
            "{{task_count}}": str(task_count),
            "{{todo_count}}": str(todo_count),
            "{{done_count}}": str(done_count),
            "{{focus_session_count}}": str(focus_session_count),
            "{{total_focus_minutes}}": str(total_focus_minutes),
            "{{current_date}}": current_date,
            "{{current_time}}": current_time,
            "{{unread_notifications}}": str(unread_notifications),
            "{{gaze_status}}": gaze_status,
            "{{theme_name}}": theme_name,
        }

        for placeholder, value in placeholders.items():
            resolved = resolved.replace(placeholder, value)

        return resolved

    def get_placeholders_metadata(self) -> list[dict[str, str]]:
        return [
            {"placeholder": "{{plugin_number}}", "description": "Number of active plugins (excluding core.demo)."},
            {"placeholder": "{{username}}", "description": "Your preferred display name (or 'abte user')."},
            {"placeholder": "{{task_count}}", "description": "Total count of all tasks in the repository."},
            {"placeholder": "{{todo_count}}", "description": "Tasks currently pending or in progress."},
            {"placeholder": "{{done_count}}", "description": "Count of completed tasks."},
            {"placeholder": "{{focus_session_count}}", "description": "Successfully completed focus sessions."},
            {"placeholder": "{{total_focus_minutes}}", "description": "Total productive minutes from completed sessions."},
            {"placeholder": "{{current_date}}", "description": "Current system date (YYYY-MM-DD)."},
            {"placeholder": "{{current_time}}", "description": "Current system time (HH:MM)."},
            {"placeholder": "{{unread_notifications}}", "description": "Count of unread notifications."},
            {"placeholder": "{{gaze_status}}", "description": "Gaze tracking status (active/inactive)."},
            {"placeholder": "{{theme_name}}", "description": "Name of the active application theme."},
        ]

