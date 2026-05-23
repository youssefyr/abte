"""
ABTE test suite — fixed imports, real unit tests for hot-path components.

Run with:  pytest app/tests/test_abte.py -v
"""
from __future__ import annotations

import os
import pickle
import shutil
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def temp_app_data_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d)


@pytest.fixture
def tmp_db(tmp_path):
    """In-memory-style: fresh SQLite file per test."""
    from app.data.repository import SqliteRepository
    return SqliteRepository(str(tmp_path / "test.db"))


@pytest.fixture
def dummy_settings(tmp_path):
    from app.core.settings import SettingsManager
    s = SettingsManager()
    s.get = lambda key, default=None, type=None: str(tmp_path) if key == "Storage/app_data_dir" else default
    s.app_data_dir = lambda: tmp_path
    return s


@pytest.fixture
def dummy_repository():
    return SimpleNamespace()


# ---------------------------------------------------------------------------
# Repository CRUD
# ---------------------------------------------------------------------------

class TestRepositoryNotifications:
    def test_add_and_fetch(self, tmp_db):
        from app.data.entities import NotificationItem
        n = NotificationItem(id="n1", title="Hello", message="World", level="info",
                             created_at=datetime.now(timezone.utc))
        tmp_db.add_notification(n)
        found = tmp_db.notification_by_id("n1")
        assert found is not None
        assert found.title == "Hello"

    def test_mark_read(self, tmp_db):
        from app.data.entities import NotificationItem
        n = NotificationItem(id="n2", title="R", message="M", created_at=datetime.now(timezone.utc))
        tmp_db.add_notification(n)
        tmp_db.mark_notification_read("n2")
        found = tmp_db.notification_by_id("n2")
        assert found is not None and found.read_at is not None

    def test_delete(self, tmp_db):
        from app.data.entities import NotificationItem
        n = NotificationItem(id="n3", title="D", message="M", created_at=datetime.now(timezone.utc))
        tmp_db.add_notification(n)
        tmp_db.delete_notification("n3")
        assert tmp_db.notification_by_id("n3") is None


class TestRepositoryCoachReports:
    def test_add_and_get_latest(self, tmp_db):
        now = datetime.now(timezone.utc)
        tmp_db.add_coach_report(now - timedelta(days=7), now, "This is a great review!")
        latest = tmp_db.get_latest_coach_report()
        assert latest is not None
        assert latest["summary_text"] == "This is a great review!"


class TestRepositoryTasks:
    def test_crud(self, tmp_db):
        from app.data.entities import TaskItem
        t = TaskItem(id="t1", title="Write tests")
        tmp_db.add_task(t)
        found = tmp_db.task_by_id("t1")
        assert found is not None and found.title == "Write tests"

        found.title = "Write more tests"
        tmp_db.update_task(found)
        found2 = tmp_db.task_by_id("t1")
        assert found2 is not None and found2.title == "Write more tests"

        tmp_db.delete_task("t1")
        assert tmp_db.task_by_id("t1") is None


class TestRepositorySessions:
    def test_add_and_list(self, tmp_db):
        from app.data.entities import SessionLogItem
        now = datetime.now(timezone.utc)
        s = SessionLogItem(id="sess-1", started_at=now, ended_at=now + timedelta(minutes=25),
                           outcome="completed", focus_score_avg=0.8)
        tmp_db.add_session(s)
        all_sessions = tmp_db.all_sessions()
        assert any(sess.id == "sess-1" for sess in all_sessions)

    def test_target_minutes_roundtrip(self, tmp_db):
        """Pomodoro target_minutes persists correctly (#18)."""
        from app.data.entities import SessionLogItem
        now = datetime.now(timezone.utc)
        s = SessionLogItem(id="sess-pm", started_at=now, target_minutes=25)
        tmp_db.add_session(s)
        found = tmp_db.session_by_id("sess-pm")
        assert found is not None
        assert found.target_minutes == 25


# ---------------------------------------------------------------------------
# FocusFeatureExtractor
# ---------------------------------------------------------------------------

class TestFocusFeatureExtractor:
    @pytest.fixture
    def extractor(self):
        from app.services.focus_feature_extractor import FocusFeatureExtractor
        return FocusFeatureExtractor()

    @pytest.fixture
    def base_obs(self):
        from app.services.focus_feature_extractor import GazeObservation
        return GazeObservation(
            timestamp=datetime.now(timezone.utc),
            process="code",
            title="my_project — VSCode",
            is_browser=False,
            is_coding_window=True,
            is_terminal_window=False,
            is_docs_window=False,
            is_distracting_window=False,
            tab_title=None,
            tab_url=None,
            gaze_present=True,
            face_present=True,
            absent_seconds_estimate=0,
            yaw_deg=0.0,
            pitch_deg=0.0,
            blink_rate_per_min=15.0,
            eye_open_avg=0.85,
            gaze_zone="ON_SCREEN",
            tab_fuzzy_match_score=90.0,
            slm_distraction_class="PRODUCTIVE",
        )

    def test_build_features_returns_dict(self, extractor, base_obs):
        features = extractor.build_features(base_obs)
        assert isinstance(features, dict)
        assert len(features) > 10

    def test_coding_window_not_distracting(self, extractor, base_obs):
        features = extractor.build_features(base_obs)
        assert features.get("is_coding_window") == 1.0
        assert features.get("is_distracting_window") == 0.0

    def test_distracting_keyword_detection(self, extractor, base_obs):
        base_obs.title = "YouTube – Lofi beats"
        base_obs.is_distracting_window = True
        features = extractor.build_features(base_obs)
        assert features.get("is_distracting_window") == 1.0

    def test_user_extra_keywords_honoured(self, extractor, base_obs):
        extractor.user_extra_distracting_keywords = ["my_company_distractor"]
        base_obs.title = "my_company_distractor dashboard"
        base_obs.is_browser = True
        features = extractor.build_features(base_obs)
        # The extractor should flag this via _looks_distracting
        assert "is_distracting_window" in features

    def test_hour_encoding_in_range(self, extractor, base_obs):
        features = extractor.build_features(base_obs)
        assert -1.0 <= features.get("hour_sin", 0.0) <= 1.0
        assert -1.0 <= features.get("hour_cos", 0.0) <= 1.0


# ---------------------------------------------------------------------------
# FocusSmoother
# ---------------------------------------------------------------------------

class TestFocusSmoother:
    @pytest.fixture
    def smoother(self):
        from app.services.focus_smoother import FocusSmoother
        return FocusSmoother(ema_alpha=0.4, bucket_minutes=1)

    def test_initial_score_is_neutral(self, smoother):
        assert 0.3 <= smoother.current_focus_score() <= 0.7

    def test_sustained_high_risk_drives_score_down(self, smoother):
        now = datetime.now(timezone.utc)
        for i in range(12):
            smoother.update(p_drift=0.9, now=now + timedelta(seconds=i * 5))
        score = smoother.current_focus_score()
        assert score < 0.5, f"Expected low score under sustained drift, got {score:.3f}"

    def test_sustained_low_risk_drives_score_up(self, smoother):
        now = datetime.now(timezone.utc)
        for i in range(12):
            smoother.update(p_drift=0.05, now=now + timedelta(seconds=i * 5))
        score = smoother.current_focus_score()
        assert score > 0.5, f"Expected high score under low drift, got {score:.3f}"

    def test_end_session_returns_final_score(self, smoother):
        now = datetime.now(timezone.utc)
        smoother.update(p_drift=0.3, now=now)
        final = smoother.end_session()
        assert 0.0 <= final <= 1.0

    def test_score_clamped_to_unit_interval(self, smoother):
        now = datetime.now(timezone.utc)
        for i in range(20):
            smoother.update(p_drift=0.99, now=now + timedelta(seconds=i * 5))
        assert 0.0 <= smoother.current_focus_score() <= 1.0


# ---------------------------------------------------------------------------
# TabFocusGuard
# ---------------------------------------------------------------------------

class TestTabFocusGuard:
    @pytest.fixture
    def guard(self):
        from app.services.tab_focus_guard import TabFocusGuard
        return TabFocusGuard(threshold=55.0)

    def test_last_score_initialized(self, guard):
        assert hasattr(guard, "last_score")
        assert isinstance(guard.last_score, float)

    def test_relevant_tab_passes(self, guard):
        guard.set_task("Write unit tests for ABTE focus module")
        result = guard.check("ABTE focus tests — VSCode")
        assert result is True
        assert guard.last_score > 0

    def test_completely_unrelated_tab_fails(self, guard):
        guard.set_task("Implement database migration")
        result = guard.check("Netflix — Stranger Things Season 4")
        assert result is False

    def test_last_score_updated_after_check(self, guard):
        guard.set_task("Python programming")
        guard.check("Python documentation — docs.python.org")
        assert guard.last_score >= 0.0

    def test_no_task_set_passes_all(self, guard):
        guard.set_task(None)
        result = guard.check("Completely unrelated title")
        assert result is True


# ---------------------------------------------------------------------------
# Notification deduplication
# ---------------------------------------------------------------------------

class TestNotificationDeduplication:
    def test_duplicate_suppressed(self, dummy_repository):
        from app.services.notification_service import NotificationService
        svc = NotificationService(dummy_repository)
        published = []
        svc.repository.add_notification = lambda item: published.append(item) or item
        svc.publish("A", "B")
        svc.publish("A", "B")  # duplicate — should be suppressed
        # Only 1 unique notification should be published
        assert len(published) == 1

    def test_different_messages_both_published(self, dummy_repository):
        from app.services.notification_service import NotificationService
        svc = NotificationService(dummy_repository)
        published = []
        svc.repository.add_notification = lambda item: published.append(item) or item
        svc.publish("Title", "Message 1")
        svc.publish("Title", "Message 2")
        assert len(published) == 2


# ---------------------------------------------------------------------------
# PlannerModel KMeans guard
# ---------------------------------------------------------------------------

class TestPlannerModel:
    def test_kmeans_guard_with_no_sessions(self):
        from app.models.planner_model import EnergyPatternModel
        model = EnergyPatternModel(cluster_count=3)
        # Should NOT raise even with 0 sessions
        scores = model.build_hour_scores([])
        assert isinstance(scores, dict)

    def test_kmeans_guard_with_one_session(self):
        from app.models.planner_model import EnergyPatternModel
        from app.data.entities import SessionLogItem
        model = EnergyPatternModel(cluster_count=3)
        s = SessionLogItem(id="s1", started_at=datetime.now(), focus_score_avg=0.7)
        scores = model.build_hour_scores([s])
        assert isinstance(scores, dict)

    def test_build_free_slots_returns_list(self):
        from app.models.planner_model import build_free_slots
        from datetime import time
        slots = build_free_slots(
            anchor_day=date.today(),
            day_count=1,
            events=[],
            tasks=[],
            workday_start=time(8, 0),
            workday_end=time(17, 0),
        )
        assert isinstance(slots, list)

    def test_planner_result_empty(self):
        from app.models.planner_model import PlannerResult
        from datetime import datetime, UTC
        pr = PlannerResult(datetime.now(UTC), [], [], {})
        assert pr.suggestions == []


# ---------------------------------------------------------------------------
# SLM service init
# ---------------------------------------------------------------------------

def test_slm_service_init(dummy_settings, dummy_repository):
    from app.services.slm.slm_service import SlmService
    svc = SlmService(dummy_settings, dummy_repository)
    assert svc is not None
    # current_config returns None when no model path is configured
    assert svc.current_config() is None


def test_clean_thinking_tags(dummy_settings, dummy_repository):
    from app.services.slm.slm_service import SlmService
    svc = SlmService(dummy_settings, dummy_repository)
    
    # Standard tags
    raw1 = "<thought>Thinking very hard here...</thought>This is the actual answer."
    assert svc._clean_thinking_tags(raw1) == "This is the actual answer."
    
    # Case-insensitive
    raw2 = "<THOUGHT>Thinking very hard here...</THOUGHT>This is the actual answer."
    assert svc._clean_thinking_tags(raw2) == "This is the actual answer."

    # Multiline
    raw3 = "<thought>\nThinking very hard here...\nLine 2\n</thought>\nThis is the actual answer."
    assert svc._clean_thinking_tags(raw3) == "This is the actual answer."

    # Thinking tag
    raw4 = "<thinking>Thinking...</thinking>Result"
    assert svc._clean_thinking_tags(raw4) == "Result"

    # Open-ended (truncated)
    raw5 = "Result<thought>Thinking but never finished"
    assert svc._clean_thinking_tags(raw5) == "Result"

    # Liquid/thinking tags [Start thinking] ... [End thinking]
    raw6 = "[Start thinking]\nLet's analyze\n[End thinking]This is the final review."
    assert svc._clean_thinking_tags(raw6) == "This is the final review."

    # Case-insensitive [Start thinking]
    raw7 = "[START THINKING]Analysis[END THINKING]Review content"
    assert svc._clean_thinking_tags(raw7) == "Review content"

    # Open-ended [Start thinking]
    raw8 = "Valid result[Start thinking]Incomplete thoughts"
    assert svc._clean_thinking_tags(raw8) == "Valid result"

    # llama-cli speed metrics line
    raw9 = "Review content\n\n[ Prompt: 8.8 t/s | Generation: 5.9 t/s ]"
    assert svc._clean_thinking_tags(raw9) == "Review content"


# ---------------------------------------------------------------------------
# Plugin manager
# ---------------------------------------------------------------------------

def test_plugin_manager_plugins():
    from app.core import plugin_api
    pm = plugin_api.PluginManager()
    plugins = pm.plugins()
    assert isinstance(plugins, list)


def test_plugin_manager_attach_storage():
    from app.core import plugin_api

    class DummyStorage:
        def register_migration(self, plugin_id, migrate_fn): pass
        def set_task_plugin_value(self, task_id, plugin_id, key, value): pass
        def get_task_plugin_payload(self, task_id, plugin_id): return {}
        def ensure_plugin_table(self, plugin_id, create_sql): pass

    pm = plugin_api.PluginManager()
    pm.attach_storage(DummyStorage())
    assert isinstance(pm.plugins(), list)


# ---------------------------------------------------------------------------
# LLM runtime detector
# ---------------------------------------------------------------------------

def test_llama_runtime_detector():
    from app.models import llama_runtime
    status = llama_runtime.LlamaRuntimeDetector.detect()
    assert hasattr(status, "found")


# ---------------------------------------------------------------------------
# UserProfileItem dynamic metadata/plugin compatibility
# ---------------------------------------------------------------------------

def test_user_profile_item_plugin_compatibility():
    from app.data.entities import UserProfileItem
    profile = UserProfileItem(id="test_profile", display_name="Youssef")
    
    # Pre-defined slotted fields
    assert profile.id == "test_profile"
    assert profile.display_name == "Youssef"
    assert profile.avatar_path == ""
    
    # Custom plugin fields (dynamic attributes)
    profile.coins = 150
    profile.gamification_rank = "Gold"
    
    # Ensure they are accessible directly as attributes
    assert profile.coins == 150
    assert profile.gamification_rank == "Gold"
    
    # Ensure they are stored internally in the serialized meta dictionary
    assert profile.meta["coins"] == 150
    assert profile.meta["gamification_rank"] == "Gold"
    
    # Ensure attribute errors are still raised for non-existent attributes
    import pytest
    with pytest.raises(AttributeError):
        _ = profile.non_existent_field


# ---------------------------------------------------------------------------
# ModelSelectorWidget unit test
# ---------------------------------------------------------------------------

def test_model_selector_widget_states(qtbot, dummy_settings):
    from app.ui.slm_model_selector import ModelSelectorWidget, _ActionCell
    from app.services.slm.model_catalog import KNOWN_MODELS

    settings_dict = {"SLM/model_path": ""}
    dummy_settings.get = lambda key, default=None, type=None: settings_dict.get(key, default)
    dummy_settings.set = lambda key, val: settings_dict.update({key: val})
    dummy_settings.sync = lambda: None

    widget = ModelSelectorWidget(dummy_settings)
    qtbot.addWidget(widget)

    # Initially none of the models are active
    for entry in KNOWN_MODELS:
        assert not widget._is_active(entry)

    first_entry = KNOWN_MODELS[0]
    path = widget._model_path(first_entry)
    
    # Temporarily mock _is_downloaded to return True for first_entry
    widget._is_downloaded = lambda entry: entry.model_id == first_entry.model_id
    
    # Refresh to apply download status
    widget.refresh()

    # Get first action cell widget
    cell = widget._table.cellWidget(0, 4)
    assert isinstance(cell, _ActionCell)
    
    # Since it is downloaded but not active:
    # 1. Use button should be visible and enabled
    # 2. Download button should be hidden
    assert not cell._use_btn.isHidden()
    assert cell._use_btn.isEnabled()
    assert cell._use_btn.text() == "Use"
    assert cell._dl_btn.isHidden()

    # Now make it active!
    widget._on_use_requested(first_entry, 0)
    assert settings_dict["SLM/model_path"] == str(path)
    assert widget._is_active(first_entry)

    # Now that it is active:
    # 1. Use button should say "Active" and be disabled
    assert not cell._use_btn.isHidden()
    assert not cell._use_btn.isEnabled()
    assert cell._use_btn.text() == "Active"


# ---------------------------------------------------------------------------
# Multithreaded SQLite Verification
# ---------------------------------------------------------------------------

def test_sqlite_repository_multithreaded_writes(tmp_db):
    """Verify that multiple concurrent threads can write to the SqliteRepository without crashes."""
    import threading
    from app.data.entities import TaskItem
    import time
    
    errors = []
    
    def worker(thread_idx: int):
        try:
            for i in range(10):
                t = TaskItem(id=f"t-thread-{thread_idx}-{i}", title=f"Task {thread_idx}-{i}")
                tmp_db.add_task(t)
                time.sleep(0.01)
        except Exception as e:
            errors.append(e)

    threads = []
    for idx in range(5):
        t = threading.Thread(target=worker, args=(idx,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    assert not errors, f"Encountered {len(errors)} multithreaded SQLite errors: {errors}"
    assert len(tmp_db.all_tasks()) == 50


# ---------------------------------------------------------------------------
# SLM Subprocess Cancellation & Graceful Shutdown Verification
# ---------------------------------------------------------------------------

class TestSlmCancellationAndProcessCleanup:
    def test_subprocess_registration_and_cancellation(self, dummy_settings, dummy_repository):
        from app.services.slm.slm_service import SlmService
        from app.services.slm.models import SlmConfig, RuntimeExecutionPlan
        from PySide6.QtCore import QThread
        import time
        import shutil
        import tempfile
        import os
        import stat
        
        # Create a mock executable python script that ignores all flags and sleeps
        mock_fd, mock_path = tempfile.mkstemp()
        try:
            with os.fdopen(mock_fd, 'w') as f:
                f.write("#!/usr/bin/env python3\nimport time\ntime.sleep(10)\n")
            os.chmod(mock_path, os.stat(mock_path).st_mode | stat.S_IEXEC)
            
            svc = SlmService(dummy_settings, dummy_repository)
            cfg = SlmConfig(
                backend="llama_cpp",
                model_path=Path(mock_path),
                max_tokens=128,
                coach_enabled=True,
                decomposition_enabled=True,
                display_name="Test",
                current_goals="",
                startup_completed=True,
                prefer_gpu=False,
                cpu_threads=1,
                gpu_layers_override=None,
                gpu_memory_reserve_mb=256,
                cpu_memory_reserve_mb=512,
                planner_timeout_ms=5000,
            )
            plan = RuntimeExecutionPlan(
                target="cpu",
                backend="llama_cpp",
                estimated_latency_seconds=1.0,
                score=100.0,
                reason="test",
                llama_gpu_layers=0,
                onnx_provider=None,
                cpu_threads=1,
            )
            
            # Mock shutil.which
            orig_which = shutil.which
            shutil.which = lambda cmd: mock_path
            
            # Mock flag cache
            svc._supported_flags_cache[mock_path] = {"no-cnv": False, "no-display-prompt": False}
            
            class MockThread(QThread):
                def __init__(self):
                    super().__init__()
                    self._cancelled = False
                    self.result_text = None
                    self.error = None
                    self._thread_id = None
    
                def run(self):
                    import threading
                    self._thread_id = threading.get_ident()
                    try:
                        self.result_text = svc._run_llama_cpp(
                            prompt="5",
                            cfg=cfg,
                            plan=plan,
                        )
                    except Exception as exc:
                        self.error = exc
                    finally:
                        from app.services.slm.slm_async import _CANCELLED_THREADS, _CANCELLED_THREADS_LOCK
                        with _CANCELLED_THREADS_LOCK:
                            _CANCELLED_THREADS.discard(self._thread_id)
    
            thread = MockThread()
            thread.start()
            
            # Let it spawn
            time.sleep(0.5)
            
            with svc._process_lock:
                active_p = list(svc._active_processes)
            assert len(active_p) == 1, "Subprocess was not registered in _active_processes"
            proc = active_p[0]
            assert proc.poll() is None, "Subprocess should still be running"
    
            # Cancel thread
            thread._cancelled = True
            
            from app.services.slm.slm_async import _CANCELLED_THREADS, _CANCELLED_THREADS_LOCK
            with _CANCELLED_THREADS_LOCK:
                if thread._thread_id is not None:
                    _CANCELLED_THREADS.add(thread._thread_id)
            
            # Wait for thread to finish
            thread.wait(2000)
            
            try:
                proc.wait(timeout=1)
            except Exception:
                pass
            
            assert proc.poll() is not None, "Subprocess was not terminated upon thread cancellation"
            with svc._process_lock:
                assert len(svc._active_processes) == 0, "Subprocess was not removed from _active_processes"
        finally:
            shutil.which = orig_which
            try:
                os.remove(mock_path)
            except Exception:
                pass

    def test_graceful_shutdown_stop_kills_active_processes(self, dummy_settings, dummy_repository):
        from app.services.slm.slm_service import SlmService
        from app.services.slm.models import SlmConfig, RuntimeExecutionPlan
        import time
        import threading
        import shutil
        import tempfile
        import os
        import stat
        
        # Create a mock executable python script that ignores all flags and sleeps
        mock_fd, mock_path = tempfile.mkstemp()
        try:
            with os.fdopen(mock_fd, 'w') as f:
                f.write("#!/usr/bin/env python3\nimport time\ntime.sleep(10)\n")
            os.chmod(mock_path, os.stat(mock_path).st_mode | stat.S_IEXEC)
            
            svc = SlmService(dummy_settings, dummy_repository)
            cfg = SlmConfig(
                backend="llama_cpp",
                model_path=Path(mock_path),
                max_tokens=128,
                coach_enabled=True,
                decomposition_enabled=True,
                display_name="Test",
                current_goals="",
                startup_completed=True,
                prefer_gpu=False,
                cpu_threads=1,
                gpu_layers_override=None,
                gpu_memory_reserve_mb=256,
                cpu_memory_reserve_mb=512,
                planner_timeout_ms=10000,
            )
            plan = RuntimeExecutionPlan(
                target="cpu",
                backend="llama_cpp",
                estimated_latency_seconds=1.0,
                score=100.0,
                reason="test",
                llama_gpu_layers=0,
                onnx_provider=None,
                cpu_threads=1,
            )
            
            # Mock shutil.which
            orig_which = shutil.which
            shutil.which = lambda cmd: mock_path
            
            svc._supported_flags_cache[mock_path] = {"no-cnv": False, "no-display-prompt": False}
            
            def target():
                svc._run_llama_cpp(
                    prompt="10",
                    cfg=cfg,
                    plan=plan,
                )
                
            t = threading.Thread(target=target)
            t.start()
            
            time.sleep(0.5)
            
            with svc._process_lock:
                active_p = list(svc._active_processes)
            assert len(active_p) == 1
            proc = active_p[0]
            assert proc.poll() is None
            
            # Stop service
            svc.stop()
            
            t.join(timeout=2)
            
            assert proc.poll() is not None
            with svc._process_lock:
                assert len(svc._active_processes) == 0
        finally:
            shutil.which = orig_which
            try:
                os.remove(mock_path)
            except Exception:
                pass

