# app/services/tab_focus_guard.py
from __future__ import annotations

import logging
import time
from typing import Optional

from app.services.extension_core import ExtensionCoreHandler

logger = logging.getLogger(__name__)


from rapidfuzz import fuzz as _fuzz 
_HAVE_FUZZ = True


# -----------------------------------------------------------------------
# Domain dwell configuration
# -----------------------------------------------------------------------

_MAIL_DOMAINS = {
    "mail.google.com", "gmail.com",
    "outlook.live.com", "outlook.office.com", "hotmail.com",
    "proton.me", "protonmail.com",
    "mail.yahoo.com",
}

_DEFAULT_DWELL_SECONDS = 5.0
_MAIL_DWELL_SECONDS = 10.0
_MATCH_THRESHOLD = 55  # rapidfuzz token_set_ratio [0-100]; below this = off-task


def _dwell_for_url(url: str) -> float:
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        if any(d in host for d in _MAIL_DOMAINS):
            return _MAIL_DWELL_SECONDS
    except Exception:
        pass
    return _DEFAULT_DWELL_SECONDS


def _relevance_score(tab_title: str, task_title: str) -> float:
    """
    Returns a [0.0, 1.0] relevance score between the active tab and the task.
    Uses rapidfuzz token_set_ratio when available, substring match otherwise.
    token_set_ratio handles word-order variance and partial matches well
    (e.g. "Learn Python in 5 Hours – YouTube" vs "learn python").
    """
    if not tab_title or not task_title:
        return 0.0

    tab = tab_title.lower().strip()
    task = task_title.lower().strip()

    if _HAVE_FUZZ:
        score: float = _fuzz.token_set_ratio(tab, task)
        return score / 100.0

    # Fallback: check if any word from the task title appears in the tab title
    task_words = [w for w in task.split() if len(w) >= 4]
    if not task_words:
        return 1.0  # too short to judge, don't penalise
    hits = sum(1 for w in task_words if w in tab)
    return hits / len(task_words)


# -----------------------------------------------------------------------
# Guard
# -----------------------------------------------------------------------

class TabFocusGuard:
    """
    Called on every focus tick when a session is active.
    Compares the active browser tab title with the session's task title using
    NLP (rapidfuzz token_set_ratio). If the tab is off-task for longer than
    the domain-specific dwell threshold, it pushes a block command through
    the ExtensionCoreHandler.

    The guard is stateless across sessions: call reset() when a session ends.
    """

    def __init__(self, extension_core: ExtensionCoreHandler) -> None:
        self._ext = extension_core
        self._off_task_since: Optional[float] = None
        self._blocked = False
        self._last_tab_url = ""

    def reset(self) -> None:
        """Call when a focus session starts or ends."""
        self._off_task_since = None
        if self._blocked:
            self._ext.push_unblock()
        self._blocked = False
        self._last_tab_url = ""

    def check(self, task_title: Optional[str]) -> None:
        """
        Called every tick (500ms). Reads the latest extension state and
        evaluates whether the current tab is on-task.
        """
        if not task_title:
            # No active task set → nothing to guard against.
            self._clear_off_task()
            return

        payload = self._ext.fetch_raw_payload()
        if payload is None:
            # Extension not connected; don't penalise.
            self._clear_off_task()
            return

        tab_title = str(payload.get("active_tab_title", ""))
        tab_url = str(payload.get("active_tab_url", ""))

        if not tab_title and not tab_url:
            self._clear_off_task()
            return

        score = _relevance_score(tab_title, task_title)
        on_task = score >= (_MATCH_THRESHOLD / 100.0)

        logger.debug(
            f"TabFocusGuard: score={score:.2f} tab='{tab_title[:60]}' task='{task_title[:60]}'"
        )

        if on_task:
            self._clear_off_task()
            if self._blocked:
                self._ext.push_unblock()
                self._blocked = False
                logger.info("TabFocusGuard: tab returned on-task, unblocking.")
            return

        # Off-task path
        now = time.time()
        if self._off_task_since is None:
            self._off_task_since = now
            self._last_tab_url = tab_url

        dwell = _dwell_for_url(tab_url)
        elapsed = now - self._off_task_since

        if elapsed >= dwell and not self._blocked:
            self._blocked = True
            self._ext.push_block(reason="off_task")
            logger.info(
                f"TabFocusGuard: blocking tab after {elapsed:.1f}s off-task "
                f"(threshold={dwell}s). Tab='{tab_title[:60]}'"
            )

    def _clear_off_task(self) -> None:
        self._off_task_since = None