"""
slm_async.py — Async SLM wrapper

Provides QThread-based workers that wrap SlmService calls so they never block
the Qt main thread. All callers should prefer these over direct SlmService calls
for any operation that triggers LLM inference.
"""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QThread, Signal


import threading

# Global set to track thread IDs of cancelled background workers
_CANCELLED_THREADS: set[int] = set()
_CANCELLED_THREADS_LOCK = threading.Lock()


class _BaseSlmWorker(QThread):
    """Base class with cancellation token support."""

    # Optimization for low end systems
    _ALL_RUNNING_WORKERS: list[_BaseSlmWorker] = []

    def __init__(self, slm_service: Any) -> None:
        super().__init__()
        self._slm = slm_service
        self._cancelled = False
        self._thread_id: int | None = None
        # Optimization for low end systems
        self.finished.connect(self._on_thread_finished)

    def start(self, priority: QThread.Priority = QThread.Priority.InheritPriority) -> None:
        # Optimization for low end systems
        if self not in _BaseSlmWorker._ALL_RUNNING_WORKERS:
            _BaseSlmWorker._ALL_RUNNING_WORKERS.append(self)
        super().start(priority)

    def _on_thread_finished(self) -> None:
        # Optimization for low end systems
        try:
            if self in _BaseSlmWorker._ALL_RUNNING_WORKERS:
                _BaseSlmWorker._ALL_RUNNING_WORKERS.remove(self)
        except Exception:
            pass

    def _register_thread(self) -> None:
        self._thread_id = threading.get_ident()

    def _unregister_thread(self) -> None:
        if self._thread_id is not None:
            with _CANCELLED_THREADS_LOCK:
                _CANCELLED_THREADS.discard(self._thread_id)

    def cancel(self) -> None:
        """Request cancellation. Inference may still complete but result is discarded."""
        self._cancelled = True
        if self._thread_id is not None:
            with _CANCELLED_THREADS_LOCK:
                _CANCELLED_THREADS.add(self._thread_id)


class DecomposeTaskWorker(_BaseSlmWorker):
    """Run goal decomposition in a background thread.

    Usage::

        worker = DecomposeTaskWorker(slm_service, goal_text)
        worker.done.connect(on_done)   # receives list[DecomposedTask]
        worker.failed.connect(on_err)  # receives error str
        worker.start()
        # To cancel: worker.cancel(); worker.quit(); worker.wait(500)
    """

    done: Signal = Signal(list)
    failed: Signal = Signal(str)

    def __init__(self, slm_service: Any, goal: str) -> None:
        super().__init__(slm_service)
        self._goal = goal

    def run(self) -> None:
        self._register_thread()
        try:
            result = self._slm.decompose_goal(self._goal)
            if not self._cancelled:
                self.done.emit(list(result) if result else [])
        except Exception as exc:
            if not self._cancelled:
                self.failed.emit(str(exc))
        finally:
            self._unregister_thread()


class GenerateWeeklyReviewWorker(_BaseSlmWorker):
    """Run weekly review generation in a background thread.

    Usage::

        worker = GenerateWeeklyReviewWorker(slm_service, week_start, week_end)
        worker.done.connect(on_done)   # receives str
        worker.failed.connect(on_err)
        worker.start()
    """

    done: Signal = Signal(str)
    failed: Signal = Signal(str)

    def __init__(self, slm_service: Any, week_start: Any, week_end: Any) -> None:
        super().__init__(slm_service)
        self._week_start = week_start
        self._week_end = week_end

    def run(self) -> None:
        self._register_thread()
        try:
            if not hasattr(self._slm, "generate_weekly_review"):
                if not self._cancelled:
                    self.failed.emit("generate_weekly_review not supported by this backend")
                return
            text = self._slm.generate_weekly_review(
                week_start=self._week_start,
                week_end=self._week_end,
            )
            if not self._cancelled:
                self.done.emit(str(text) if text else "")
        except Exception as exc:
            if not self._cancelled:
                self.failed.emit(str(exc))
        finally:
            self._unregister_thread()


class CategorizeDistractionsWorker(_BaseSlmWorker):
    """Categorise a batch of window titles in a background thread.

    Usage::

        worker = CategorizeDistractionsWorker(slm_service, titles)
        worker.done.connect(on_done)   # receives dict[str, str]
        worker.start()
    """

    done: Signal = Signal(dict)
    failed: Signal = Signal(str)

    def __init__(self, slm_service: Any, titles: list[str]) -> None:
        super().__init__(slm_service)
        self._titles = titles

    def run(self) -> None:
        self._register_thread()
        try:
            if hasattr(self._slm, "categorize_distractions"):
                result: dict[str, str] = self._slm.categorize_distractions(self._titles)
            else:
                result = {}
            if not self._cancelled:
                self.done.emit(result)
        except Exception as exc:
            if not self._cancelled:
                self.failed.emit(str(exc))
        finally:
            self._unregister_thread()


class SlmWorkerPool:
    """Manages a single in-flight SLM worker, cancelling stale requests on new arrival.

    This prevents build-up of queued inference calls when the user triggers
    multiple actions quickly. Only one worker runs at a time per pool.

    Usage::

        pool = SlmWorkerPool()
        pool.submit(DecomposeTaskWorker(slm, goal))
        worker.done.connect(handler)
    """

    def __init__(self) -> None:
        self._current: _BaseSlmWorker | None = None

    def submit(self, worker: _BaseSlmWorker) -> _BaseSlmWorker:
        """Cancel any in-flight worker and start the new one."""
        if self._current is not None and self._current.isRunning():
            self._current.cancel()
            self._current.quit()
            self._current.wait(500)
        self._current = worker
        worker.finished.connect(self._on_finished)
        worker.start()
        return worker

    def _on_finished(self) -> None:
        self._current = None

    @property
    def is_busy(self) -> bool:
        return self._current is not None and self._current.isRunning()
