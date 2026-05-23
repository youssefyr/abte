# app/tests/test_sidebar_template.py
from __future__ import annotations

import unittest
from unittest.mock import MagicMock
from datetime import datetime
from app.services.sidebar_template_service import SidebarTemplateService


class TestSidebarTemplateService(unittest.TestCase):
    def setUp(self):
        self.mock_repo = MagicMock()
        self.mock_settings = MagicMock()
        self.mock_pm = MagicMock()
        self.mock_gaze = MagicMock()

        self.service = SidebarTemplateService(
            repository=self.mock_repo,
            settings=self.mock_settings,
            plugin_manager=self.mock_pm,
            gaze_service=self.mock_gaze,
        )

    def test_basic_render_no_placeholder(self):
        text = self.service.render("Hello World", "JohnDoe")
        self.assertEqual(text, "Hello World")

    def test_username_placeholder(self):
        text = self.service.render("Welcome {{username}}!", "JohnDoe")
        self.assertEqual(text, "Welcome JohnDoe!")

    def test_empty_template(self):
        text = self.service.render("", "JohnDoe")
        self.assertEqual(text, "")

    def test_plugin_number_placeholder(self):
        p1 = MagicMock()
        p1.id = "core.demo"
        p2 = MagicMock()
        p2.id = "my.cool.plugin"
        p3 = MagicMock()
        p3.id = "another.plugin"

        self.mock_pm.plugins.return_value = [p1, p2, p3]
        text = self.service.render("Plugins: {{plugin_number}}", "JohnDoe")
        self.assertEqual(text, "Plugins: 2")

    def test_task_counts(self):
        t1 = MagicMock()
        t1.status = "done"
        t2 = MagicMock()
        t2.status = "todo"
        t3 = MagicMock()
        t3.status = "in_progress"

        self.mock_repo.all_tasks.return_value = [t1, t2, t3]
        text = self.service.render("Tasks: {{task_count}} | Todo: {{todo_count}} | Done: {{done_count}}", "John")
        self.assertEqual(text, "Tasks: 3 | Todo: 2 | Done: 1")

    def test_current_date_time(self):
        text = self.service.render("Date: {{current_date}} Time: {{current_time}}", "John")
        now = datetime.now()
        expected_date = now.strftime("%Y-%m-%d")
        expected_time = now.strftime("%H:%M")
        self.assertIn(expected_date, text)
        self.assertIn(expected_time, text)

    def test_get_placeholders_metadata(self):
        meta = self.service.get_placeholders_metadata()
        self.assertEqual(len(meta), 12)
        placeholders = [m["placeholder"] for m in meta]
        self.assertIn("{{plugin_number}}", placeholders)
        self.assertIn("{{username}}", placeholders)
        self.assertIn("{{task_count}}", placeholders)
        self.assertIn("{{todo_count}}", placeholders)
        self.assertIn("{{done_count}}", placeholders)
        self.assertIn("{{focus_session_count}}", placeholders)
        self.assertIn("{{total_focus_minutes}}", placeholders)
        self.assertIn("{{current_date}}", placeholders)
        self.assertIn("{{current_time}}", placeholders)
        self.assertIn("{{unread_notifications}}", placeholders)
        self.assertIn("{{gaze_status}}", placeholders)
        self.assertIn("{{theme_name}}", placeholders)
