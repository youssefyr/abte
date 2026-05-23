from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.services.slm import SlmService


def apply_development_bootstrap(settings: Any, repository: Any) -> None:
    repository._run_core_migrations()
    if bool(settings.get("Development/dev_reset_database", False)):
        repository.reset_all_data()
        settings.set("Development/dev_reset_database", False)
        settings.sync()

    use_fake = bool(settings.get("Development/dev_fake_data", False))
    fake_tasks = bool(settings.get("Development/dev_fake_data_tasks", False))
    fake_sessions = bool(settings.get("Development/dev_fake_data_sessions", False))
    if use_fake or fake_tasks or fake_sessions:
        repository.seed_fake_data()
        settings.set("Development/dev_fake_data_tasks", False)
        settings.set("Development/dev_fake_data_sessions", False)
        settings.sync()


def maybe_generate_weekly_review(settings: Any, repository: Any) -> str:
    slm = SlmService(settings, repository)
    if not slm.is_model_ready():
        return ""
    today = date.today()
    week_end = today
    week_start = today - timedelta(days=6)
    return slm.generate_weekly_review(week_start, week_end)


def decompose_goal_task(settings: Any, repository: Any, goal_task: Any) -> list[Any]:
    slm = SlmService(settings, repository)
    return slm.decompose_and_persist_goal(goal_task, repository.create_task)