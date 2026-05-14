# app/core/window_tracker.py
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OSWindowState:
    title: str
    process: str
    is_browser: bool


class BaseWindowTracker:
    BROWSER_PROCESSES = {
        "chrome", "firefox", "msedge", "brave", "safari", "opera", "chromium",
        "zen", "zen-alpha", "librewolf", "waterfox", "floorp", "mullvad",
        "vivaldi", "thorium", "arc", "yandex", "ungoogled-chromium"
    }

    def __init__(self) -> None:
        self._last_state = OSWindowState(title="Unknown", process="unknown", is_browser=False)
        self._last_update_time = 0.0
        self._cache_ttl = 0.4

    def get_active_window(self) -> OSWindowState:
        now = time.time()
        if (now - self._last_update_time) < self._cache_ttl:
            return self._last_state

        try:
            state = self._fetch_native_state()
            normalized = self._normalize_process_name(state.process)
            state.process = normalized or "unknown"
            state.is_browser = self._is_browser_process(state.process)
            self._last_state = state
            self._last_update_time = now
            return state
        except Exception as e:
            logger.debug(f"Native window tracker failed, returning cached state: {e}")
            return self._last_state

    def _normalize_process_name(self, process: str) -> str:
        value = (process or "").strip().lower()
        if value.endswith(".exe"):
            value = value[:-4]
        aliases = {
            "google-chrome": "chrome",
            "google-chrome-stable": "chrome",
            "chrome-browser": "chrome",
            "microsoft-edge": "msedge",
            "microsoft-edge-dev": "msedge",
            "microsoft-edge-beta": "msedge",
            "brave-browser": "brave",
            "firefox-esr": "firefox",
            "org.mozilla.firefox": "firefox",
            "org.mozilla.librewolf": "librewolf",
            "librewolf-bin": "librewolf",
            "zen-browser": "zen",
            "waterfox-bin": "waterfox",
            "floorp-bin": "floorp",
            "thorium-browser": "thorium",
            "chromium-browser": "chromium",
            "org.chromium.chromium": "chromium",
        }
        return aliases.get(value, value)

    def _is_browser_process(self, process: str) -> bool:
        value = self._normalize_process_name(process)
        if not value:
            return False
        if value in self.BROWSER_PROCESSES:
            return True
        return any(browser in value for browser in self.BROWSER_PROCESSES)

    def _fetch_native_state(self) -> OSWindowState:
        return OSWindowState(title="", process="unknown", is_browser=False)


class WindowsTracker(BaseWindowTracker):
    def __init__(self) -> None:
        super().__init__()
        import ctypes
        self.user32 = ctypes.windll.user32  # type: ignore
        self.kernel32 = ctypes.windll.kernel32  # type: ignore
        self.PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def _fetch_native_state(self) -> OSWindowState:
        import ctypes
        from ctypes import wintypes

        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return OSWindowState(title="", process="", is_browser=False)

        length = self.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value

        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        process_name = "unknown"
        h_process = self.kernel32.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if h_process:
            exe_buf = ctypes.create_unicode_buffer(260)
            size = wintypes.DWORD(260)
            if self.kernel32.QueryFullProcessImageNameW(h_process, 0, exe_buf, ctypes.byref(size)):
                process_name = os.path.basename(exe_buf.value).lower().replace(".exe", "")
            self.kernel32.CloseHandle(h_process)

        return OSWindowState(title=title, process=process_name, is_browser=False)


class LinuxTracker(BaseWindowTracker):
    def __init__(self) -> None:
        super().__init__()
        self.is_wayland = (
            os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            or "WAYLAND_DISPLAY" in os.environ
        )
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        self.desktop = desktop

        # Global foreground support is only realistic on:
        # - X11
        # - Wayland + KDE (KWin DBus)
        # - Wayland + wlroots compositors with lswt available
        if not self.is_wayland:
            self.supports_global_foreground = True
        else:
            has_lswt = shutil.which("lswt") is not None
            is_kde = "kde" in desktop
            # GNOME and others: assume unsupported unless you ship your own extension
            self.supports_global_foreground = bool(has_lswt or is_kde)

    def _fetch_native_state(self) -> OSWindowState:
        if self.is_wayland:
            # If we know we cannot get global foreground, just return unknown
            if not getattr(self, "supports_global_foreground", False):
                return OSWindowState(title="", process="unknown", is_browser=False)
            return self._fetch_wayland()
        return self._fetch_x11()

    def _fetch_x11(self) -> OSWindowState:
        try:
            root_out = subprocess.check_output(
                ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                stderr=subprocess.DEVNULL,
                timeout=0.2,
            ).decode("utf-8", errors="ignore")

            win_id = root_out.strip().split()[-1]
            if win_id == "0x0":
                return OSWindowState(title="", process="unknown", is_browser=False)

            win_out = subprocess.check_output(
                ["xprop", "-id", win_id, "_NET_WM_NAME", "WM_NAME", "WM_CLASS", "_NET_WM_PID"],
                stderr=subprocess.DEVNULL,
                timeout=0.2,
            ).decode("utf-8", errors="ignore")

            title = ""
            wm_class_instance = ""
            wm_class_name = ""
            pid: int | None = None

            for line in win_out.splitlines():
                if "_NET_WM_NAME" in line or line.startswith("WM_NAME"):
                    parsed_title = line.split("=", 1)[-1].strip().strip('"')
                    if parsed_title:
                        title = parsed_title
                elif "WM_CLASS" in line:
                    parts = [p.strip().strip('"') for p in line.split("=", 1)[-1].split(",")]
                    if len(parts) >= 1:
                        wm_class_instance = parts[0].lower()
                    if len(parts) >= 2:
                        wm_class_name = parts[1].lower()
                elif "_NET_WM_PID" in line:
                    try:
                        pid = int(line.split("=", 1)[-1].strip())
                    except ValueError:
                        pid = None

            process = "unknown"

            if pid:
                process = self._process_name_from_pid(pid) or "unknown"

            if process == "unknown":
                process = self._normalize_process_name(wm_class_instance or wm_class_name or "unknown")

            if not title:
                title = wm_class_name or wm_class_instance or ""

            return OSWindowState(title=title, process=process, is_browser=False)

        except (subprocess.SubprocessError, FileNotFoundError, IndexError):
            fallback = self._fetch_xdotool()
            if fallback is not None:
                return fallback
            return OSWindowState(title="", process="unknown", is_browser=False)

    def _fetch_xdotool(self) -> OSWindowState | None:
        if shutil.which("xdotool") is None:
            return None
        try:
            win_id = subprocess.check_output(
                ["xdotool", "getactivewindow"],
                stderr=subprocess.DEVNULL,
                timeout=0.2,
            ).decode("utf-8").strip()
            if not win_id:
                return None

            title = subprocess.check_output(
                ["xdotool", "getwindowname", win_id],
                stderr=subprocess.DEVNULL,
                timeout=0.2,
            ).decode("utf-8", errors="ignore").strip()

            pid_raw = subprocess.check_output(
                ["xdotool", "getwindowpid", win_id],
                stderr=subprocess.DEVNULL,
                timeout=0.2,
            ).decode("utf-8").strip()

            pid = int(pid_raw) if pid_raw.isdigit() else None
            process = self._process_name_from_pid(pid) if pid else "unknown"

            return OSWindowState(
                title=title,
                process=self._normalize_process_name(process or "unknown"),
                is_browser=False,
            )
        except (subprocess.SubprocessError, FileNotFoundError, ValueError):
            return None

    def _process_name_from_pid(self, pid: int) -> str | None:
        try:
            comm_path = Path(f"/proc/{pid}/comm")
            name = comm_path.read_text(encoding="utf-8").strip().lower()
            if name:
                return self._normalize_process_name(name)
        except Exception:
            pass

        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", errors="ignore")
            first = cmdline.split("\x00", 1)[0].strip()
            if first:
                return self._normalize_process_name(Path(first).name)
        except Exception:
            pass

        return None

    def _fetch_wayland(self) -> OSWindowState:
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        title, process = "", "unknown"

        try:
            wlroots_state = self._fetch_wlroots()
            if wlroots_state is not None:
                return wlroots_state

            if "kde" in desktop:
                out = subprocess.check_output(
                    ["qdbus", "org.kde.KWin", "/KWin", "org.kde.KWin.activeWindow"],
                    stderr=subprocess.DEVNULL,
                    timeout=0.2,
                ).decode("utf-8").strip()
                if out:
                    info = subprocess.check_output(
                        ["qdbus", "org.kde.KWin", "/KWin", "org.kde.KWin.windowInfo", out],
                        stderr=subprocess.DEVNULL,
                        timeout=0.2,
                    ).decode("utf-8", errors="ignore")
                    for line in info.splitlines():
                        if "caption" in line or "title" in line:
                            title = line.split(":", 1)[-1].strip()
                        if "resourceClass" in line or "windowClass" in line or "appId" in line:
                            process = self._normalize_process_name(line.split(":", 1)[-1].strip().lower())

            elif "gnome" in desktop:
                pass

        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        return OSWindowState(title=title, process=process, is_browser=False)

    def _fetch_wlroots(self) -> OSWindowState | None:
        if shutil.which("lswt") is None:
            return None
        try:
            out = subprocess.check_output(
                ["lswt", "-j"],
                stderr=subprocess.DEVNULL,
                timeout=0.2,
            ).decode("utf-8")
            windows = json.loads(out)
            for win in windows:
                if win.get("activated"):
                    title = win.get("title", "")
                    process = self._normalize_process_name(win.get("app-id", "unknown").lower())
                    return OSWindowState(title=title, process=process, is_browser=False)
        except (subprocess.SubprocessError, FileNotFoundError, json.JSONDecodeError):
            return None
        return None


class MacTracker(BaseWindowTracker):
    def _fetch_native_state(self) -> OSWindowState:
        return OSWindowState(title="Mac Window", process="mac_app", is_browser=False)


def get_window_tracker() -> BaseWindowTracker:
    if sys.platform.startswith("win32"):
        return WindowsTracker()
    elif sys.platform.startswith("linux"):
        return LinuxTracker()
    elif sys.platform == "darwin":
        return MacTracker()
    return BaseWindowTracker()