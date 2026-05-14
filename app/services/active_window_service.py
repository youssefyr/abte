# app/services/active_window_service.py
from __future__ import annotations

import logging
import time
from collections import Counter, deque
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.core.window_tracker import get_window_tracker, OSWindowState
from app.services.extension_core import ExtensionCoreHandler

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ExtensionData:
    active_tab_url: str
    active_tab_title: str
    is_productive: bool
    tab_switch_count_5m: int
    focus_score_hint: float


@dataclass(slots=True)
class ActiveWindowSample:
    timestamp: float
    title: str
    process: str
    is_browser: bool


class ActiveWindowService:
    def __init__(self, extension_core: Optional[ExtensionCoreHandler] = None) -> None:
        self._os_tracker = get_window_tracker()
        self._ext = extension_core or ExtensionCoreHandler()

        self._window_history: deque[ActiveWindowSample] = deque(maxlen=240)
        self._last_os_window: Optional[OSWindowState] = None
        self._last_extension_data: Optional[ExtensionData] = None
        self._session_start_time = time.time()
        self._last_window_fingerprint: Optional[tuple[str, str]] = None
        self._last_window_change_time = self._session_start_time
        self._recent_window_changes: deque[float] = deque(maxlen=400)

        self._firefox_family = {
            "firefox", "zen", "zen-alpha", "librewolf",
            "waterfox", "floorp", "mullvad",
        }
        self._chromium_family = {
            "chrome", "chromium", "brave", "msedge", "vivaldi",
            "thorium", "arc", "yandex", "ungoogled-chromium", "opera",
        }

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def read_active_window(self) -> Dict[str, Any]:
        try:
            os_window = self._os_tracker.get_active_window()

            # If extension data is fresh, treat window as browser unconditionally
            ext_data = self._fetch_extension_data()
            if ext_data and not os_window.is_browser:
                os_window = OSWindowState(
                    title=os_window.title,
                    process=os_window.process,
                    is_browser=True,
                )

            self._last_os_window = os_window
            self._last_extension_data = ext_data
            self._record_window_sample(os_window)
            self._record_window_change(os_window)

            now = time.time()
            idle_seconds = max(0.0, now - self._last_window_change_time)
            app_switch_frequency_5m = float(self._count_recent_switches(300))
            productive_hit = self._is_productive_context(os_window)
            focus_score = 0.65 if productive_hit else 0.45

            result: Dict[str, Any] = {
                "title": os_window.title,
                "process": os_window.process,
                "process_tags": self._process_tags_for(os_window),
                "tab_switch_frequency_5m": 0.0,
                "app_switch_frequency_5m": app_switch_frequency_5m,
                "idle_seconds": idle_seconds,
                "focus_score_window_5m": focus_score,
                "focus_score_5m": focus_score,
                "current_session_minutes": (now - self._session_start_time) / 60.0,
                "productive_keyword_hit": productive_hit,
                "user_override_hit": False,
            }

            if os_window.is_browser and ext_data:
                result["title"] = ext_data.active_tab_title or result["title"]
                result["process_tags"] = "research, reading"
                result["tab_switch_frequency_5m"] = float(ext_data.tab_switch_count_5m)
                result["productive_keyword_hit"] = ext_data.is_productive
                result["focus_score_window_5m"] = ext_data.focus_score_hint
                result["focus_score_5m"] = ext_data.focus_score_hint
            elif os_window.is_browser:
                result["process_tags"] = "browser, unknown_context"

            return result

        except Exception as exc:
            logger.error(f"ActiveWindowService.read_active_window failed: {exc}")
            return self._safe_fallback()

    def get_last_os_window(self) -> Optional[OSWindowState]:
        return self._last_os_window

    def get_last_extension_data(self) -> Optional[ExtensionData]:
        return self._last_extension_data

    def get_recent_window_stats(self, window_seconds: int = 60) -> Dict[str, Any]:
        now = time.time()
        samples = [s for s in self._window_history if (now - s.timestamp) <= window_seconds]
        if not samples:
            return {"samples": 0, "by_process": [], "top_titles": [], "browser_family": "unknown", "last_seen": None}

        process_counts = Counter(s.process for s in samples if s.process)
        title_counts = Counter(s.title for s in samples if s.title)

        ff_hits = sum(process_counts.get(p, 0) for p in self._firefox_family)
        cr_hits = sum(process_counts.get(p, 0) for p in self._chromium_family)
        browser_family = "firefox" if ff_hits > cr_hits else "chromium" if cr_hits > ff_hits else "unknown"

        return {
            "samples": len(samples),
            "by_process": process_counts.most_common(6),
            "top_titles": title_counts.most_common(6),
            "browser_family": browser_family,
            "last_seen": max(s.timestamp for s in samples),
        }

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    def _fetch_extension_data(self) -> Optional[ExtensionData]:
        payload = self._ext.fetch_raw_payload()
        if payload is None:
            return None
        try:
            return ExtensionData(
                active_tab_url=str(payload.get("active_tab_url", "")),
                active_tab_title=str(payload.get("active_tab_title", "")),
                is_productive=bool(payload.get("is_productive", False)),
                tab_switch_count_5m=int(payload.get("tab_switch_count_5m", 0)),
                focus_score_hint=float(payload.get("focus_score_hint", 0.5)),
            )
        except Exception:
            return None

    def _record_window_sample(self, os_window: OSWindowState) -> None:
        self._window_history.append(
            ActiveWindowSample(
                timestamp=time.time(),
                title=os_window.title or "",
                process=os_window.process or "unknown",
                is_browser=bool(os_window.is_browser),
            )
        )

    def _record_window_change(self, os_window: OSWindowState) -> None:
        fingerprint = (os_window.process or "unknown", os_window.title or "")
        if fingerprint != self._last_window_fingerprint:
            now = time.time()
            self._recent_window_changes.append(now)
            self._last_window_change_time = now
            self._last_window_fingerprint = fingerprint

    def _count_recent_switches(self, window_seconds: int) -> int:
        now = time.time()
        while self._recent_window_changes and (now - self._recent_window_changes[0]) > window_seconds:
            self._recent_window_changes.popleft()
        return len(self._recent_window_changes)

    def _process_tags_for(self, os_window: OSWindowState) -> str:
        process = (os_window.process or "").lower()
        title = (os_window.title or "").lower()
        if os_window.is_browser:
            return "browser"
        if any(t in process for t in ["code", "pycharm", "idea", "sublime", "vim", "nvim", "emacs"]):
            return "coding, work"
        if any(t in process for t in ["terminal", "konsole", "gnome-terminal", "wezterm", "kitty", "alacritty"]):
            return "terminal, work"
        if any(t in title for t in ["docs", "spec", "design", "ticket"]):
            return "planning, work"
        return "general"

    def _is_productive_context(self, os_window: OSWindowState) -> bool:
        title = (os_window.title or "").lower()
        process = (os_window.process or "").lower()
        if any(k in title for k in ["github", "gitlab", "bitbucket", "jira", "confluence", "notion", "docs", "design", "spec", "ticket", "figma"]):
            return True
        if any(k in process for k in ["code", "pycharm", "idea", "terminal", "konsole", "wezterm", "kitty", "alacritty"]):
            return True
        return False

    def _safe_fallback(self) -> Dict[str, Any]:
        return {
            "title": "Unknown",
            "process": "unknown",
            "process_tags": "unknown",
            "tab_switch_frequency_5m": 0.0,
            "app_switch_frequency_5m": 0.0,
            "idle_seconds": 0.0,
            "focus_score_window_5m": 0.5,
            "focus_score_5m": 0.5,
            "current_session_minutes": (time.time() - self._session_start_time) / 60.0,
            "productive_keyword_hit": False,
            "user_override_hit": False,
        }
    def is_wayland_session(self) -> bool:
        return bool(getattr(self._os_tracker, "is_wayland", False))