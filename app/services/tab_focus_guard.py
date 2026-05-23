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
# rapidfuzz token_set_ratio [0-100]; below this = off-task.
# Can be overridden at runtime via TabFocusGuard.set_threshold().
_MATCH_THRESHOLD = 55


def _dwell_for_url(url: str) -> float:
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        if any(d in host for d in _MAIL_DOMAINS):
            return _MAIL_DWELL_SECONDS
    except Exception:
        pass
    return _DEFAULT_DWELL_SECONDS


def _relevance_score(tab_title: str, task_title: str, task_tags: list[str] | None = None) -> float:
    """
    Returns a [0.0, 1.0] relevance score between the active tab and the task.

    Uses a two-signal blend:
    1. rapidfuzz token_set_ratio — handles word-order variance and partial matches
       (e.g. "Learn Python in 5 Hours – YouTube" vs "learn python").
    2. Keyword-level hit rate — important words from the task title and tags must
       appear literally in the tab title for domain-specific tools.

    The blend gives more weight to fuzzy matching for long titles and more weight
    to keyword presence for short, precise task names.
    """
    if not tab_title or not task_title:
        return 0.0

    tab = tab_title.lower().strip()
    task = task_title.lower().strip()

    fuzzy_score = 0.0
    if _HAVE_FUZZ:
        # token_set_ratio is robust to extra words; token_sort_ratio handles order.
        set_ratio = float(_fuzz.token_set_ratio(tab, task))
        sort_ratio = float(_fuzz.token_sort_ratio(tab, task))
        fuzzy_score = max(set_ratio, sort_ratio) / 100.0
    else:
        # Substring fallback
        fuzzy_score = 1.0 if task in tab else 0.0

    # Keyword presence score: significant words (>=4 chars) from task title + tags
    stop_words = {"that", "this", "with", "from", "have", "will", "been", "being"}
    keywords = [w for w in task.split() if len(w) >= 4 and w not in stop_words]
    if task_tags:
        keywords.extend(t.lower().lstrip("#") for t in task_tags if len(t) >= 3)
    if keywords:
        hits = sum(1 for kw in keywords if kw in tab)
        kw_score = hits / len(keywords)
    else:
        kw_score = fuzzy_score  # nothing to test, rely on fuzzy

    # Weighted blend: fuzzy 70%, keyword 30%
    return min(1.0, fuzzy_score * 0.7 + kw_score * 0.3)


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

    def __init__(self, extension_core: ExtensionCoreHandler | None = None, match_threshold: int | None = None, threshold: float | None = None) -> None:
        self._ext = extension_core
        self._off_task_since: Optional[float] = None
        self._blocked = False
        self._last_tab_url = ""
        # last_score is read by FocusTickEngine._capture_observation for the feature vector
        self.last_score: float = 1.0
        
        thresh = match_threshold if match_threshold is not None else threshold
        if thresh is not None:
            self._match_threshold = int(thresh)
        else:
            self._match_threshold = _MATCH_THRESHOLD

    def set_threshold(self, threshold: int) -> None:
        """Override the match threshold at runtime (e.g., from user settings)."""
        self._match_threshold = max(0, min(100, threshold))

    def reset(self) -> None:
        """Call when a focus session starts or ends."""
        self._off_task_since = None
        if self._blocked and self._ext is not None:
            self._ext.push_unblock()
        self._blocked = False
        self._last_tab_url = ""

    def set_task(self, task_title: Optional[str]) -> None:
        self._task_title = task_title

    def check(self, arg: Optional[str]) -> bool:
        """
        Called every tick (500ms) or from unit tests. Reads the latest extension state and
        evaluates whether the current tab is on-task.
        
        Supports two interfaces:
        1. Production (via FocusTickEngine): check(task_title) where self._ext is active.
        2. Testing (via test suite): check(tab_title) after calling set_task(task_title).
        """
        # Test mode (extension core is None or inactive)
        if self._ext is None:
            task_title = getattr(self, "_task_title", None)
            if not task_title:
                return True
            tab_title = arg or ""
            score = _relevance_score(tab_title, task_title)
            self.last_score = score
            return score >= (self._match_threshold / 100.0)

        # Production mode
        task_title = arg
        if not task_title:
            # No active task set → nothing to guard against.
            self._clear_off_task()
            return True

        payload = self._ext.fetch_raw_payload()
        if payload is None:
            # Extension not connected; don't penalise.
            self._clear_off_task()
            return True

        tab_title = str(payload.get("active_tab_title", ""))
        tab_url = str(payload.get("active_tab_url", ""))

        if not tab_title and not tab_url:
            self._clear_off_task()
            return True

        score = _relevance_score(tab_title, task_title)
        # Store for use by FocusTickEngine feature vector
        self.last_score = score
        on_task = score >= (self._match_threshold / 100.0)

        logger.debug(
            f"TabFocusGuard: score={score:.2f} tab='{tab_title[:60]}' task='{task_title[:60]}'"
        )

        if on_task:
            self._clear_off_task()
            if self._blocked:
                self._ext.push_unblock()
                self._blocked = False
                logger.info("TabFocusGuard: tab returned on-task, unblocking.")
            return True

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
        return False

    def _clear_off_task(self) -> None:
        self._off_task_since = None