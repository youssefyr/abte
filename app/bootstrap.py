# app/bootstrap.py
from __future__ import annotations
from pathlib import Path
from typing import Any
import sys
import logging

from app.core.settings import SettingsManager
from app.data.repository import SqliteRepository
from app.core.plugin_api import PluginManager
from app.core.integration_hooks import apply_development_bootstrap
from app.services.extension_core import ExtensionCoreHandler
from app.services.active_window_service import ActiveWindowService
from app.services.focus_session_service import FocusSessionService
from app.services.focus_tick_engine import FocusTickEngine
from app.services.gaze_service import GazeService
from app.services.slm import SlmService
from app.services.tab_focus_guard import TabFocusGuard
from app.ui.main_window import MainWindow


logger = logging.getLogger(__name__)

def _resolve_model_path(preferred: Path) -> Path | None:
    """
    Returns the first existing model path, checking:
      1. The user's app-data dir (preferred, survives updates).
      2. The bundled path inside the package (works with PyInstaller/Nuitka).
    Returns None if neither exists, so the engine starts in degraded mode.
    """
    if preferred.exists():
        return preferred


    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled = Path(getattr(sys, "_MEIPASS")) / "app" / "models" / "focus_drift_model.txt"
    else:
        # Walk up from this file to the package root, then into app/models.
        bundled = Path(__file__).resolve().parent / "app" / "models" / "focus_drift_model.txt"
        if not bundled.exists():
            # Also try relative to the bootstrap file itself (flat layout).
            bundled = Path(__file__).resolve().parent / "models" / "focus_drift_model.txt"

    return bundled if bundled.exists() else None


def _resolve_face_model_path(preferred: Path) -> Path | None:
    if preferred.exists():
        return preferred

    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled = Path(getattr(sys, "_MEIPASS")) / "app" / "models" / "face_landmarker.task"
    else:
        bundled = Path(__file__).resolve().parent / "models" / "face_landmarker.task"
    return bundled if bundled.exists() else None

def build_focus_runtime(
    *,
    repository: Any,
    settings: Any,
    active_window_service: ActiveWindowService,
    gaze_service: Any | None,
    extension_core: ExtensionCoreHandler,
) -> tuple[FocusSessionService, FocusTickEngine]:
    preferred_model_path = Path(settings.app_data_dir()) / "models" / "focus_drift_model.txt"
    resolved_model_path = _resolve_model_path(preferred_model_path)

    if resolved_model_path is None:
        logger.warning(
            "focus_drift_model.txt not found in app-data (%s) or bundled package. "
            "Engine will start in degraded mode (rule-based fallback only).",
            preferred_model_path,
        )

    tab_guard = TabFocusGuard(extension_core=extension_core)

    focus_tick_engine = FocusTickEngine(
        repository=repository,
        active_window_service=active_window_service,
        gaze_service=gaze_service,
        focus_session_service=None,
        model_path=resolved_model_path,
        tick_interval_ms=500,
        model_interval_ticks=4,
        tab_focus_guard=tab_guard,  # FocusTickEngine must accept this kwarg (see note below)
    )

    focus_session_service = FocusSessionService(
        repository=repository,
    )

    focus_tick_engine._focus_session_service = focus_session_service
    focus_session_service.set_focus_tick_engine(focus_tick_engine)

    # ── Wire gaze signals ────────────────────────────────────────────────────
    # set_gaze_service: connects gaze_updated → FocusSessionService._on_gaze_updated
    #   (auto-pause / auto-resume based on face absence)
    # FocusTickEngine.on_session_started already calls gaze_service.start()
    # so we do NOT call attach_session_service() here — that would start gaze twice.
    if gaze_service is not None:
        focus_session_service.set_gaze_service(gaze_service)

    # Reset tab guard on session boundaries
    focus_session_service.session_started.connect(lambda _s: tab_guard.reset())
    focus_session_service.session_ended.connect(lambda _s: tab_guard.reset())

    # ── Wire session task to extension ───────────────────────────────────────
    # Push the active task title/keywords to the extension when a session starts
    # so the extension can do real-time native URL/title matching for blocking.
    def _on_session_started_ext(session: object) -> None:
        try:
            task_id = getattr(session, "planned_task_id", None)
            task_title = ""
            if task_id:
                task = repository.task_by_id(task_id)
                if task:
                    task_title = str(getattr(task, "title", "") or "")
            # Generate keywords from title words (>= 4 chars)
            stop_words = {"the", "and", "for", "with", "that", "this", "from", "have",
                          "will", "are", "was", "were", "been", "being", "has", "had"}
            keywords = [
                w.lower() for w in task_title.split()
                if len(w) >= 4 and w.lower() not in stop_words
            ] if task_title else []
            extension_core.push_task(task_title, keywords)
            logger.info("ExtensionCore: pushed task to extension: %r", task_title)
        except Exception as exc:
            logger.debug("ExtensionCore: push_task failed: %s", exc)

    def _on_session_ended_ext(_session: object) -> None:
        try:
            extension_core.push_clear_task()
        except Exception:
            pass

    focus_session_service.session_started.connect(_on_session_started_ext)
    focus_session_service.session_ended.connect(_on_session_ended_ext)

    focus_tick_engine.start()
    logger.info(
        "FocusTickEngine started. model_loaded=%s path=%s",
        focus_tick_engine._model.is_loaded,
        resolved_model_path,
    )
    return focus_session_service, focus_tick_engine


def build_app() -> tuple[MainWindow, dict[str, object]]:
    settings = SettingsManager()
    repository = SqliteRepository(settings.settings.database_path())
    apply_development_bootstrap(settings, repository)

    plugin_manager = PluginManager()
    plugin_manager.attach_storage(repository)
    slm_service = SlmService(settings, repository)
    for plugin in plugin_manager.plugin_runtimes():
        if plugin.migrate_fn is not None:
            repository.register_plugin_migration(plugin.item.id, plugin.migrate_fn)

    # Shared extension handler — single instance for entire app lifetime
    extension_core = ExtensionCoreHandler()

    window_service = ActiveWindowService(extension_core=extension_core)

    preferred_face_model = Path(
        str(
            settings.get(
                "Vision/face_landmarker_model_path",
                str(settings.app_data_dir() / "models" / "face_landmarker.task"),
            )
            or str(settings.app_data_dir() / "models" / "face_landmarker.task")
        )
    )
    resolved_face_model = _resolve_face_model_path(preferred_face_model) or preferred_face_model
    gaze_model_path = str(resolved_face_model)
    camera_value = settings.get("Vision/camera_index", 0)
    gaze_camera_index = int(camera_value) if isinstance(camera_value, (int, str)) else 0
    gaze_data_dir = settings.app_data_dir() / "vision"
    gaze_data_dir.mkdir(parents=True, exist_ok=True)

    gaze_service = GazeService(
        model_path=gaze_model_path,
        data_dir=gaze_data_dir,
        camera_index=gaze_camera_index,
    )

    # Apply the enable_gaze setting before wiring session signals
    gaze_enabled = bool(settings.get("Vision/enable_gaze", False))
    gaze_service.set_enabled(gaze_enabled)

    focus_session_service, focus_tick_engine = build_focus_runtime(
        repository=repository,
        settings=settings,
        active_window_service=window_service,
        gaze_service=gaze_service,
        extension_core=extension_core,
    )

    window = MainWindow(
        repository=repository,
        settings=settings,
        plugin_manager=plugin_manager,
        slm_service=slm_service,
        active_window_service=window_service,
        focus_session_service=focus_session_service,
        gaze_service=gaze_service,
        focus_tick_engine=focus_tick_engine,
    )

    services = {
        "extension_core": extension_core,
        "window_service": window_service,
        "gaze_service": gaze_service,
        "settings": settings,
        "repository": repository,
        "plugin_manager": plugin_manager,
        "slm_service": slm_service,
        "focus_session_service": focus_session_service,
        "focus_tick_engine": focus_tick_engine,
    }

    return window, services