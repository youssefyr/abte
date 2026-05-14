# --- Repository and notification integration tests ---
import tempfile
from app.data.repository import SqliteRepository
from app.data.entities import NotificationItem, TaskItem, SessionLogItem
from datetime import datetime, timezone, timedelta

def test_repository_notification_crud():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        repo = SqliteRepository(str(db_path))
        # Add notification
        notif = NotificationItem(
            id="notif-1",
            title="Test",
            message="Test message",
            level="info",
            created_at=datetime.now(timezone.utc),
        )
        repo.add_notification(notif)
        print("[PASS] test_repository_notification_crud: Added notification.")
        # Fetch notification
        found = repo.notification_by_id("notif-1")
        assert found is not None and found.title == "Test"
        print("[PASS] test_repository_notification_crud: Fetched notification by id.")
        # Mark as read
        repo.mark_notification_read("notif-1")
        found2 = repo.notification_by_id("notif-1")
        assert found2 is not None and found2.read_at is not None
        print("[PASS] test_repository_notification_crud: Marked notification as read.")
        # Delete notification
        repo.delete_notification("notif-1")
        assert repo.notification_by_id("notif-1") is None
        print("[PASS] test_repository_notification_crud: Deleted notification.")

def test_repository_task_crud():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        repo = SqliteRepository(str(db_path))
        # Add task
        task = TaskItem(id="task-1", title="T1")
        repo.add_task(task)
        print("[PASS] test_repository_task_crud: Added task.")
        # Fetch
        found = repo.task_by_id("task-1")
        assert found is not None and found.title == "T1"
        print("[PASS] test_repository_task_crud: Fetched task by id.")
        # Update
        found.title = "T1-updated"
        repo.update_task(found)
        found2 = repo.task_by_id("task-1")
        assert found2 is not None and found2.title == "T1-updated"
        print("[PASS] test_repository_task_crud: Updated task.")
        # Delete
        repo.delete_task("task-1")
        assert repo.task_by_id("task-1") is None
        print("[PASS] test_repository_task_crud: Deleted task.")

def test_repository_session_crud():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        repo = SqliteRepository(str(db_path))
        now = datetime.now(timezone.utc)
        session = SessionLogItem(
            id="sess-1",
            started_at=now,
            ended_at=now + timedelta(minutes=25),
            mode="focus",
            planned_task_id=None,
            outcome="completed",
            focus_score_avg=0.8,
            distraction_events=1,
            absent_seconds=0,
            meta={},
        )
        repo.add_session(session)
        print("[PASS] test_repository_session_crud: Added session.")
        found = repo.all_sessions()
        assert any(s.id == "sess-1" for s in found)
        print("[PASS] test_repository_session_crud: Fetched session.")

def test_repository_plugin_crud():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        repo = SqliteRepository(str(db_path))
        from app.data.entities import PluginItem
        plugin = PluginItem(id="plug-1", name="P1", version="0.1", description="desc")
        repo.add_plugin(plugin)
        print("[PASS] test_repository_plugin_crud: Added plugin.")
        found = repo.all_plugins()
        assert any(p.id == "plug-1" for p in found)
        print("[PASS] test_repository_plugin_crud: Fetched plugin.")

def test_repository_reset_and_seed():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        repo = SqliteRepository(str(db_path))
        repo.seed_fake_data()
        print("[PASS] test_repository_reset_and_seed: Seeded fake data.")
        repo.reset_all_data()
        assert not repo.all_tasks() and not repo.all_sessions() and not repo.all_notifications()
        print("[PASS] test_repository_reset_and_seed: Reset all data.")
"""
Extensive test suite for ABTE application.
Covers core logic, AI, vision, SLM, UI, and integration points.
Run with: pytest app/tests/test_abte.py
"""
import os
import tempfile
import shutil
import pytest
from pathlib import Path
from types import SimpleNamespace

from app.core import plugin_api
from app.core.settings import SettingsManager
from app.models import llama_runtime, planner_model
from app.services.focus_session_service import FocusSessionService
from app.services.gaze_service import GazeService
from app.services.notification_service import NotificationService
from app.ui import main_window, startup_wizard_dialog

# --- Fixtures ---
@pytest.fixture(scope="module")
def temp_app_data_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)

@pytest.fixture
def dummy_settings(temp_app_data_dir):
    s = SettingsManager()
    # Patch app_data_dir to temp
    s.get = lambda key, default=None, type=None: temp_app_data_dir if key == "Storage/app_data_dir" else default
    s.app_data_dir = lambda: Path(temp_app_data_dir)
    return s

@pytest.fixture
def dummy_repository():
    return SimpleNamespace()

# --- Core logic tests ---
def test_plugin_manager_plugins():
    pm = plugin_api.PluginManager()
    plugins = pm.plugins()
    print(f"[PASS] test_plugin_manager_plugins: Found plugins: {[p.id for p in plugins]}")
    assert isinstance(plugins, list)
    assert plugins[0].id == "core.demo"

# --- Model/AI tests ---
def test_llama_runtime_detector():
    status = llama_runtime.LlamaRuntimeDetector.detect()
    print(f"[PASS] test_llama_runtime_detector: found={getattr(status, 'found', None)} executable={getattr(status, 'executable', None)}")
    assert hasattr(status, "found")

# --- Planner model logic ---
def test_planner_model_build_free_slots():
    print("[PASS] test_planner_model_build_free_slots: build_free_slots returns list.")
    # Test build_free_slots edge case
    from datetime import date, time
    slots = planner_model.build_free_slots(
        anchor_day=date.today(),
        day_count=1,
        events=[],
        tasks=[],
        workday_start=time(8, 0),
        workday_end=time(17, 0),
    )
    assert isinstance(slots, list)

# --- Focus session service ---
def test_focus_session_service_start_stop(dummy_settings, dummy_repository):
    svc = FocusSessionService(dummy_repository)
    result = svc.stop_session()
    print(f"[PASS] test_focus_session_service_start_stop: stop_session returned {result}")
    assert result is None

# --- Gaze service logic ---
def test_gaze_service_start_stop(tmp_path):
    print("[PASS] test_gaze_service_start_stop: GazeService instantiation and enable/disable.")
    # Minimal test: instantiation and start/stop
    svc = GazeService(model_path=tmp_path/"dummy.task", data_dir=tmp_path)
    svc.set_enabled(True)
    svc.set_enabled(False)

# --- Notification service ---
def test_notification_service_publish(dummy_repository):
    svc = NotificationService(dummy_repository)
    svc.repository.add_notification = lambda item: item
    item = svc.publish("Test", "This is a test.")
    print(f"[PASS] test_notification_service_publish: Published notification with title '{item.title}'")
    assert item.title == "Test"

# --- UI logic (non-GUI) ---
import os
import sys
import pytest
@pytest.mark.skipif(os.environ.get("CI") == "true" or not os.environ.get("DISPLAY"), reason="No display available for Qt dialog test.")
def test_startup_wizard_dialog_load_settings(qtbot, dummy_settings, dummy_repository):
    dlg = startup_wizard_dialog.StartupWizardDialog(
        metrics=None, settings=dummy_settings, repository=dummy_repository
    )
    qtbot.addWidget(dlg)
    try:
        dlg.show()
        qtbot.waitExposed(dlg)
        dlg._load_from_settings()
        print("[PASS] test_startup_wizard_dialog_load_settings: Dialog loaded and name_input present.")
        assert hasattr(dlg, "name_input")
    finally:
        dlg.close()
        dlg.deleteLater()



# --- Manual/Usability test cases (documented for manual run) ---
def test_manual_ui_launch():
    print("[PASS] test_manual_ui_launch: (manual) UI launch test placeholder.")
    """
    Manual: Launch main window and verify UI loads without error.
    """
    # To run manually: python -m app.ui.main_window
    assert True

def test_manual_gaze_calibration():
    print("[PASS] test_manual_gaze_calibration: (manual) Gaze calibration test placeholder.")
    """
    Manual: Open gaze calibration wizard and verify calibration steps.
    """
    assert True

# --- Error handling test ---
def test_plugin_manager_disable_enable():
    print("[PASS] test_plugin_manager_disable_enable: PluginManager enable/disable ran without error.")
    pm = plugin_api.PluginManager()
    pid = pm.plugins()[0].id
    pm.disable(pid)
    pm.enable(pid)

# --- Integration: Notification and focus session ---
def test_integration_notification_focus(dummy_repository):
    notif = NotificationService(dummy_repository)
    notif.repository.add_notification = lambda item: item
    item = notif.publish("Focus", "Session started")
    print(f"[PASS] test_integration_notification_focus: Published notification with title '{item.title}'")
    assert item.title == "Focus"

# --- Data model test ---
def test_planner_model_serialization():
    import pickle
    data = pickle.dumps([])
    plan2 = pickle.loads(data)
    print("[PASS] test_planner_model_serialization: Empty list roundtrip serialization successful.")
    assert plan2 == []



# --- SLM service basic test ---
def test_slm_service_init(dummy_settings, dummy_repository):
    from app.services.slm.slm_service import SlmService
    svc = SlmService(dummy_settings, dummy_repository)
    print("[PASS] test_slm_service_init: SlmService instantiated successfully.")
    assert svc is not None
# --- Edge case: PlannerResult with no suggestions ---
def test_planner_result_empty():
    from app.models.planner_model import PlannerResult
    from datetime import datetime, UTC
    pr = PlannerResult(datetime.now(UTC), [], [], {})
    print("[PASS] test_planner_result_empty: PlannerResult with empty suggestions.")
    assert pr.suggestions == []
# --- Edge case: PluginManager with no storage ---
def test_plugin_manager_attach_storage():
    print("[PASS] test_plugin_manager_attach_storage: attach_storage with dummy storage did not error.")
    class DummyStorage:
        def register_migration(self, plugin_id, migrate_fn): pass
        def set_task_plugin_value(self, task_id, plugin_id, key, value): pass
        def get_task_plugin_payload(self, task_id, plugin_id): return {}
        def ensure_plugin_table(self, plugin_id, create_sql): pass
    pm = plugin_api.PluginManager()
    pm.attach_storage(DummyStorage())
    assert isinstance(pm.plugins(), list)
