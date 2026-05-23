from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any
import json
import shutil
import subprocess
import os
import sys
import signal
import time
import threading
import selectors
import queue
from PySide6.QtCore import QThread

from .models import resolve_llama_binary

from .benchmark_store import BenchmarkStore
from .hardware_planner import (
    assess_feasibility,
    plan_execution,
    prompt_bucket_for,
    sample_cpu,
    sample_gpu,
    SystemFeasibilityResult,
)
from .models import (
    BackEnd,
    BenchmarkSummary,
    CoachStats,
    DecomposedTask,
    PlannerDiagnostics,
    RuntimeExecutionPlan,
    SlmConfig,
)
from .parser_utils import (
    fallback_decompose_task,
    fallback_extract_tasks_from_text,
    normalize_task_draft,
    parse_json_list,
)


class AsynchronousFileReader(threading.Thread):
    def __init__(self, fd: Any, q: queue.Queue[str]) -> None:
        super().__init__()
        self._fd = fd
        self._queue = q
        self.daemon = True

    def run(self) -> None:
        try:
            while True:
                # Read chunks of text
                chunk = self._fd.read(1024)
                if not chunk:
                    break
                self._queue.put(chunk)
        except Exception:
            pass


class SlmService:
    def __init__(self, settings: Any, repository: Any) -> None:
        self._settings = settings
        self._repository = repository
        self._last_plan: RuntimeExecutionPlan | None = None
        self._last_diagnostics: PlannerDiagnostics | None = None
        self._benchmark_store = BenchmarkStore(self._settings.app_data_dir() / "slm")
        self._supported_flags_cache: dict[str, dict[str, bool]] = {}
        self._active_processes: set[subprocess.Popen] = set()
        self._process_lock = threading.Lock()

    def _register_process(self, process: subprocess.Popen) -> None:
        with self._process_lock:
            self._active_processes.add(process)

    def _unregister_process(self, process: subprocess.Popen) -> None:
        with self._process_lock:
            self._active_processes.discard(process)

    def _terminate_process(self, process: subprocess.Popen) -> None:
        try:
            if sys.platform.startswith("win"):
                process.kill()
            else:
                pgid = os.getpgid(process.pid)
                os.killpg(pgid, signal.SIGKILL)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def stop(self) -> None:
        """Forcefully terminate all active llama.cpp subprocesses during application exit or worker termination."""
        with self._process_lock:
            processes = list(self._active_processes)
        for process in processes:
            self._terminate_process(process)

    @property
    def last_plan(self) -> RuntimeExecutionPlan | None:
        return self._last_plan

    @property
    def last_diagnostics(self) -> PlannerDiagnostics | None:
        return self._last_diagnostics

    def benchmark_store(self) -> BenchmarkStore:
        return self._benchmark_store

    def current_config(self) -> SlmConfig | None:
        model_raw = str(self._settings.get("SLM/model_path", "") or "").strip()
        if not model_raw or not Path(model_raw).exists():
            from .model_catalog import KNOWN_MODELS, find_downloaded_model
            for entry in KNOWN_MODELS:
                found = find_downloaded_model(entry, self._settings.app_data_dir())
                if found:
                    model_raw = str(found)
                    self._settings.set("SLM/model_path", model_raw)
                    self._settings.sync()
                    break

        if not model_raw:
            return None

        backend_raw = str(self._settings.get("SLM/backend", "llama_cpp") or "llama_cpp").strip()
        backend: BackEnd = "onnx_runtime" if backend_raw == "onnx_runtime" else "llama_cpp"

        max_tokens_raw = self._settings.get("SLM/max_tokens", 512)
        try:
            max_tokens = max(128, min(int(max_tokens_raw or 512), 2048))
        except Exception:
            max_tokens = 512

        gpu_layers_override_raw = self._settings.get("SLM/gpu_layers_override", None)
        try:
            gpu_layers_override = int(gpu_layers_override_raw) if gpu_layers_override_raw not in ("", None) else None
        except Exception:
            gpu_layers_override = None

        cpu_threads_raw = self._settings.get("SLM/cpu_threads", None)
        try:
            cpu_threads = int(cpu_threads_raw) if cpu_threads_raw not in ("", None) else None
        except Exception:
            cpu_threads = None

        return SlmConfig(
            backend=backend,
            model_path=Path(model_raw).expanduser(),
            max_tokens=max_tokens,
            coach_enabled=bool(self._settings.get("SLM/coach_enabled", False)),
            decomposition_enabled=bool(self._settings.get("SLM/decomposition_enabled", False)),
            display_name=str(self._settings.get("Profile/display_name", "") or "").strip(),
            current_goals=str(self._settings.get("Profile/current_goals", "") or "").strip(),
            startup_completed=bool(self._settings.get("Startup/first_run_completed", False)),
            prefer_gpu=bool(self._settings.get("SLM/prefer_gpu", True)),
            cpu_threads=cpu_threads,
            gpu_layers_override=gpu_layers_override,
            gpu_memory_reserve_mb=max(256, int(self._settings.get("SLM/gpu_memory_reserve_mb", 1024) or 1024)),
            cpu_memory_reserve_mb=max(256, int(self._settings.get("SLM/cpu_memory_reserve_mb", 1024) or 1024)),
            planner_timeout_ms=max(50, int(self._settings.get("SLM/planner_timeout_ms", 250) or 250)),
        )

    def describe_benchmark_summary(self) -> str:
        cfg = self.current_config()
        if cfg is None:
            return "No SLM configuration loaded."
        summary = self._benchmark_store.summarize(
            model_path=str(cfg.model_path),
            backend=cfg.backend,
            prompt_bucket="medium",
        )
        if summary is None:
            return "No benchmark history yet."
        return (
            f"Best target: {summary.best_target or 'n/a'} | "
            f"CPU median: {summary.cpu_median_seconds or 'n/a'}s | "
            f"GPU median: {summary.gpu_median_seconds or 'n/a'}s | "
            f"Hybrid median: {summary.hybrid_median_seconds or 'n/a'}s | "
            f"Runs: {summary.record_count}"
        )

    def get_benchmark_candidates(self, *, prompt: str | None = None) -> list[RuntimeExecutionPlan]:
        cfg = self.current_config()
        if cfg is None or not cfg.model_path.exists():
            return []

        prompt_text = prompt or self._benchmark_prompt(cfg)
        diagnostics = plan_execution(cfg, prompt_text, self._benchmark_store)
        self._last_diagnostics = diagnostics
        self._last_plan = diagnostics.plan

        candidates: list[RuntimeExecutionPlan] = []
        base = diagnostics.plan
        candidates.append(base)

        if cfg.backend == "llama_cpp":
            if base.target != "cpu":
                candidates.append(
                    RuntimeExecutionPlan(
                        target="cpu",
                        backend=cfg.backend,
                        estimated_latency_seconds=base.estimated_latency_seconds,
                        score=base.score,
                        reason="Forced CPU benchmark.",
                        llama_gpu_layers=0,
                        onnx_provider=None,
                        cpu_threads=base.cpu_threads,
                    )
                )
            if base.target != "gpu":
                candidates.append(
                    RuntimeExecutionPlan(
                        target="gpu",
                        backend=cfg.backend,
                        estimated_latency_seconds=base.estimated_latency_seconds,
                        score=base.score,
                        reason="Forced GPU benchmark.",
                        llama_gpu_layers=-1 if cfg.prefer_gpu else 0,
                        onnx_provider=None,
                        cpu_threads=base.cpu_threads,
                    )
                )
            if base.target != "hybrid":
                candidates.append(
                    RuntimeExecutionPlan(
                        target="hybrid",
                        backend=cfg.backend,
                        estimated_latency_seconds=base.estimated_latency_seconds,
                        score=base.score,
                        reason="Forced hybrid benchmark.",
                        llama_gpu_layers=cfg.gpu_layers_override if cfg.gpu_layers_override is not None else 20,
                        onnx_provider=None,
                        cpu_threads=base.cpu_threads,
                    )
                )

        deduped: dict[tuple[str, int], RuntimeExecutionPlan] = {}
        for candidate in candidates:
            deduped[(candidate.target, candidate.llama_gpu_layers)] = candidate

        return list(deduped.values())

    def run_single_benchmark_candidate(
        self,
        candidate: RuntimeExecutionPlan,
        *,
        prompt: str | None = None,
    ) -> dict[str, Any]:
        cfg = self.current_config()
        if cfg is None or not cfg.model_path.exists():
            return {}
        prompt_text = prompt or self._benchmark_prompt(cfg)
        result = self._benchmark_single_plan(cfg, prompt_text, candidate)
        self._save_benchmark_summary_to_settings()
        return result

    def benchmark_runtime(self, *, prompt: str | None = None) -> list[dict[str, Any]]:
        candidates = self.get_benchmark_candidates(prompt=prompt)
        results: list[dict[str, Any]] = []
        for candidate in candidates:
            res = self.run_single_benchmark_candidate(candidate, prompt=prompt)
            results.append(res)
        return results

    def _save_benchmark_summary_to_settings(self) -> None:
        cfg = self.current_config()
        if cfg is None:
            return
        summary = self._benchmark_store.summarize(
            model_path=str(cfg.model_path),
            backend=cfg.backend,
            prompt_bucket="medium",
        )
        self._settings.set("SLM/benchmark_summary", self.describe_benchmark_summary())
        self._settings.set("SLM/benchmark_count", summary.record_count if summary else 0)
        self._settings.sync()

    def _benchmark_single_plan(
        self,
        cfg: SlmConfig,
        prompt: str,
        plan: RuntimeExecutionPlan,
    ) -> dict[str, Any]:
        cpu = sample_cpu()
        gpu = sample_gpu()
        started = datetime.utcnow().isoformat()
        t0 = perf_counter()
        success = False
        error: str | None = None
        output = ""

        try:
            if cfg.backend == "onnx_runtime":
                output = self._run_onnx_runtime(prompt, cfg, plan)
            else:
                output = self._run_llama_cpp(prompt, cfg, plan)
            success = bool(output)
        except Exception as exc:
            error = str(exc)

        duration = perf_counter() - t0

        record = self._benchmark_store.create_record(
            created_at=started,
            model_path=str(cfg.model_path),
            backend=cfg.backend,
            target=plan.target,
            prompt_bucket=prompt_bucket_for(prompt, cfg.max_tokens),
            max_tokens=cfg.max_tokens,
            cpu_utilization_percent=cpu.utilization_percent,
            cpu_free_memory_mb=cpu.free_memory_mb,
            gpu_name=gpu.name,
            gpu_utilization_percent=gpu.utilization_percent,
            gpu_free_memory_mb=gpu.free_memory_mb,
            duration_seconds=round(duration, 4),
            success=success,
            error=error,
        )
        self._benchmark_store.append(record)

        return {
            "target": plan.target,
            "duration_seconds": round(duration, 4),
            "success": success,
            "error": error,
            "reason": plan.reason,
        }

    def _benchmark_prompt(self, cfg: SlmConfig) -> str:
        payload = {
            "display_name": cfg.display_name,
            "current_goals": cfg.current_goals,
            "task": "Break the goal into three short tasks.",
            "goal": "Prepare next week plan and organize work items.",
        }
        return (
            "Return JSON only. "
            "Return {\"items\": [{\"title\": str, \"description\": str, \"estimated_minutes\": int, \"tags\": [str], \"energy_cost\": int}]}. "
            f"Input: {json.dumps(payload, ensure_ascii=False)}"
        )

    def planner_explain(self, *, prompt: str | None = None) -> dict[str, Any]:
        cfg = self.current_config()
        if cfg is None or not cfg.model_path.exists():
            return {
                "ready": False,
                "reason": "Model configuration is missing or file does not exist.",
            }
        diagnostics = plan_execution(cfg, prompt or self._benchmark_prompt(cfg), self._benchmark_store)
        self._last_diagnostics = diagnostics
        self._last_plan = diagnostics.plan
        summary = diagnostics.benchmark_summary
        return {
            "ready": True,
            "target": diagnostics.plan.target,
            "estimated_latency_seconds": diagnostics.plan.estimated_latency_seconds,
            "reason": diagnostics.plan.reason,
            "benchmark_weight": diagnostics.benchmark_weight,
            "heuristic_weight": diagnostics.heuristic_weight,
            "benchmark_summary": None
            if summary is None
            else {
                "best_target": summary.best_target,
                "cpu_median_seconds": summary.cpu_median_seconds,
                "gpu_median_seconds": summary.gpu_median_seconds,
                "hybrid_median_seconds": summary.hybrid_median_seconds,
                "record_count": summary.record_count,
            },
            "notes": diagnostics.notes,
        }

    def should_show_startup_setup(self) -> bool:
        force = bool(self._settings.get("Development/dev_show_startup_wizard", False))
        cfg = self.current_config()
        if force:
            return True
        if cfg is None:
            return True
        if not cfg.startup_completed:
            return True
        if (cfg.coach_enabled or cfg.decomposition_enabled) and not cfg.model_path.exists():
            return True
        return False

    def is_model_ready(self) -> bool:
        cfg = self.current_config()
        return bool(cfg and cfg.model_path.exists())

    def assess_system_feasibility(self) -> SystemFeasibilityResult | None:
        """
        Run a pre-benchmark system assessment.

        Returns None if no SLM config is set. Otherwise returns a
        SystemFeasibilityResult describing whether the configured model is
        safe to benchmark and listing lighter alternatives for constrained systems.
        """
        cfg = self.current_config()
        if cfg is None:
            return None
        return assess_feasibility(cfg)

    def decompose_task(
        self,
        *,
        title: str,
        description: str,
        estimated_minutes: int,
        max_subtasks: int,
        tags: list[str],
        priority: int,
        energy_cost: int,
        current_focus_score: float = 0.5,
    ) -> list[dict[str, Any]]:
        cfg = self.current_config()
        clean_title = title.strip()
        if not clean_title:
            return []
        if cfg is None or not cfg.decomposition_enabled or not cfg.model_path.exists():
            return fallback_decompose_task(
                title=clean_title,
                description=description,
                estimated_minutes=estimated_minutes,
                max_subtasks=max_subtasks,
                tags=tags,
                priority=priority,
                energy_cost=energy_cost,
            )

        bounded_subtasks = max(1, int(max_subtasks))
        prompt = self._build_structured_task_decomposition_prompt(
            cfg=cfg,
            title=clean_title,
            description=description.strip(),
            estimated_minutes=max(5, int(estimated_minutes)),
            max_subtasks=bounded_subtasks,
            tags=tags,
            priority=priority,
            energy_cost=energy_cost,
            current_focus_score=current_focus_score,
        )
        raw = self._run_llm(prompt, cfg, task_type="decomposition")
        if raw:
            parsed = parse_json_list(raw)
            normalized = [normalize_task_draft(item) for item in parsed]
            normalized = [item for item in normalized if item.get("title")]
            if normalized:
                return normalized[:bounded_subtasks]

        return fallback_decompose_task(
            title=clean_title,
            description=description,
            estimated_minutes=estimated_minutes,
            max_subtasks=bounded_subtasks,
            tags=tags,
            priority=priority,
            energy_cost=energy_cost,
        )

    def extract_tasks_from_text(
        self,
        text: str,
        *,
        default_tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        raw_text = text.strip()
        if not raw_text:
            return []

        cfg = self.current_config()
        tags = default_tags or []

        if cfg is not None and cfg.decomposition_enabled and cfg.model_path.exists():
            prompt = self._build_nl_task_prompt(cfg, raw_text, tags)
            raw = self._run_llm(prompt, cfg, task_type="decomposition")
            if raw:
                parsed = parse_json_list(raw)
                normalized = [normalize_task_draft(item) for item in parsed]
                normalized = [item for item in normalized if item.get("title")]
                if normalized:
                    return normalized

        return fallback_extract_tasks_from_text(raw_text, tags)

    def decompose_goal(self, goal_text: str) -> list[DecomposedTask]:
        cfg = self.current_config()
        if cfg is None or not cfg.decomposition_enabled or not cfg.model_path.exists():
            return []
        goal = goal_text.strip()
        if not goal:
            return []
        prompt = self._build_decomposition_prompt(goal, cfg)
        raw = self._run_llm(prompt, cfg, task_type="decomposition")
        if not raw:
            return []
        return self._parse_decomposition(raw)

    def decompose_and_persist_goal(self, goal_task: Any, create_task_fn: Any) -> list[Any]:
        items = self.decompose_goal(str(getattr(goal_task, "title", "") or ""))
        created: list[Any] = []
        for item in items:
            created_task = create_task_fn(
                title=item.title,
                description=item.description,
                estimated_minutes=item.estimated_minutes,
                tags=item.normalized_tags(),
                parent_task_id=getattr(goal_task, "id", None),
                energy_cost=item.energy_cost,
                source="slm",
                meta={"captured_from": "slm_decomposition"},
            )
            created.append(created_task)
        return created

    def build_weekly_stats(self, week_start: date, week_end: date) -> CoachStats:
        sessions = getattr(self._repository, "all_sessions", lambda: [])()
        tasks = getattr(self._repository, "all_tasks", lambda: [])()

        total_minutes = 0
        total_focus = 0.0
        focus_count = 0
        completed_tasks = 0
        created_tasks = 0
        distraction_events = 0
        absent_seconds = 0
        hourly_buckets: dict[int, int] = {}
        tag_buckets: dict[str, int] = {}

        for sess in sessions:
            started_at = getattr(sess, "started_at", None)
            if started_at is None or not (week_start <= started_at.date() <= week_end):
                continue
            ended_at = getattr(sess, "ended_at", None) or (started_at + timedelta(minutes=25))
            total_minutes += max(5, int((ended_at - started_at).total_seconds() // 60))
            score = getattr(sess, "focus_score_avg", None)
            if score is not None:
                total_focus += float(score)
                focus_count += 1
            distraction_events += int(getattr(sess, "distraction_events", 0) or 0)
            absent_seconds += int(getattr(sess, "absent_seconds", 0) or 0)
            hourly_buckets[started_at.hour] = hourly_buckets.get(started_at.hour, 0) + 1

        for task in tasks:
            created_at = getattr(task, "created_at", None)
            if created_at is not None and week_start <= created_at.date() <= week_end:
                created_tasks += 1

            completed_at = getattr(task, "completed_at", None)
            if completed_at is not None and week_start <= completed_at.date() <= week_end:
                completed_tasks += 1

            for tag in getattr(task, "tags", []) or []:
                clean = str(tag).strip().lower().lstrip("#")
                if clean:
                    tag_buckets[clean] = tag_buckets.get(clean, 0) + 1

        top_focus_hours = [hour for hour, _count in sorted(hourly_buckets.items(), key=lambda x: x[1], reverse=True)[:6]]
        top_tags = [tag for tag, _count in sorted(tag_buckets.items(), key=lambda x: x[1], reverse=True)[:6]]
        avg_focus = (total_focus / focus_count) if focus_count else None

        return CoachStats(
            week_start=week_start,
            week_end=week_end,
            total_sessions=sum(hourly_buckets.values()),
            total_minutes=total_minutes,
            avg_focus_score=avg_focus,
            top_focus_hours=top_focus_hours,
            top_tags=top_tags,
            completed_tasks=completed_tasks,
            created_tasks=created_tasks,
            distraction_events=distraction_events,
            absent_seconds=absent_seconds,
        )

    def generate_weekly_review(self, week_start: date, week_end: date) -> str:
        cfg = self.current_config()
        if cfg is None or not cfg.coach_enabled or not cfg.model_path.exists():
            return ""
        stats = self.build_weekly_stats(week_start, week_end)
        prompt = self._build_weekly_review_prompt(stats, cfg)
        # Optimization for low end systems
        review = self._run_llm(prompt, cfg, is_weekly_review=True, task_type="weekly_review").strip()
        if review:
            start_dt = datetime.combine(week_start, datetime.min.time(), tzinfo=UTC)
            end_dt = datetime.combine(week_end, datetime.max.time(), tzinfo=UTC)
            add_report = getattr(self._repository, "add_coach_report", None)
            if callable(add_report):
                add_report(
                    start_dt,
                    end_dt,
                    review,
                    {
                        "model_path": str(cfg.model_path),
                        "backend": cfg.backend,
                        "generated_locally": True,
                        "runtime_plan": self._last_plan.reason if self._last_plan else None,
                    },
                )
        return review

    def _build_decomposition_prompt(self, goal: str, cfg: SlmConfig) -> str:
        payload = {
            "display_name": cfg.display_name,
            "current_goals": cfg.current_goals,
            "goal": goal,
        }
        return (
            "You are a supportive productivity assistant. "
            "You are not a therapist, clinician, or medical advisor. "
            "Break the user's goal into small actionable tasks. "
            "Return only valid JSON using this schema: "
            "{\"items\": [{\"title\": str, \"description\": str, \"estimated_minutes\": int, \"tags\": [str], \"energy_cost\": int}]}. "
            "Use 3 to 8 tasks. Estimated minutes must be between 5 and 240. Energy cost must be 1 to 5.\n"
            f"Input: {json.dumps(payload, ensure_ascii=False)}"
        )

    def categorize_distractions(self, window_titles: list[str]) -> dict[str, str]:
        cfg = self.current_config()
        if cfg is None or not cfg.model_path.exists() or not window_titles:
            return {}
            
        payload = {"titles": window_titles}
        prompt = (
            "You are an AI that classifies application window titles into two categories: 'productive' or 'distracting'. "
            "Return a JSON object where the keys are the exact window titles provided, and the values are either 'productive' or 'distracting'. "
            "Return JSON only.\n"
            f"Input: {json.dumps(payload, ensure_ascii=False)}"
        )
        raw = self._run_llm(prompt, cfg, task_type="classification")
        if not raw:
            return {}
            
        text = raw.strip()
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last != -1 and last > first:
            text = text[first : last + 1]
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {}

    def predictive_schedule(self, backlog_tasks: list[Any], stats: CoachStats) -> list[str]:
        cfg = self.current_config()
        if cfg is None or not cfg.model_path.exists() or not backlog_tasks:
            return []
            
        tasks_payload = [
            {
                "id": str(getattr(t, "id", "")),
                "title": str(getattr(t, "title", "")),
                "energy_cost": int(getattr(t, "energy_cost", 3) or 3),
                "estimated_minutes": int(getattr(t, "estimated_minutes", 30) or 30)
            }
            for t in backlog_tasks
        ]
        
        payload = {
            "top_focus_hours": stats.top_focus_hours,
            "tasks": tasks_payload
        }
        
        prompt = (
            "You are an AI planner. You are given a user's peak focus hours and a list of tasks. "
            "Select the 3 most optimal tasks to schedule next. Match high energy tasks with peak focus hours if the current time is near peak focus. "
            "Return a JSON object: {\"selected_task_ids\": [\"id1\", \"id2\", \"id3\"]}. "
            "Return JSON only.\n"
            f"Input: {json.dumps(payload, ensure_ascii=False)}"
        )
        
        raw = self._run_llm(prompt, cfg, task_type="scheduling")
        if not raw:
            return []
            
        text = raw.strip()
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last != -1 and last > first:
            text = text[first : last + 1]
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed.get("selected_task_ids", [])
        except Exception:
            pass
        return []

    def _build_structured_task_decomposition_prompt(
        self,
        *,
        cfg: SlmConfig,
        title: str,
        description: str,
        estimated_minutes: int,
        max_subtasks: int,
        tags: list[str],
        priority: int,
        energy_cost: int,
        current_focus_score: float = 0.5,
    ) -> str:
        payload = {
            "display_name": cfg.display_name,
            "current_goals": cfg.current_goals,
            "title": title,
            "description": description,
            "estimated_minutes": estimated_minutes,
            "max_subtasks": max_subtasks,
            "tags": tags,
            "priority": priority,
            "energy_cost": energy_cost,
            "current_focus_score": current_focus_score,
        }
        
        focus_instruction = ""
        if current_focus_score < 0.6:
            focus_instruction = "The user's current focus is low. Break tasks into smaller, 5-minute chunks with low energy cost (1-2) to build momentum. "

        return (
            "You are helping break a productivity task into smaller actionable subtasks. "
            "You are not a therapist, clinician, or medical advisor. "
            f"{focus_instruction}"
            "Return JSON only. "
            "Return a JSON array of objects. "
            "Each object must contain exactly these keys: "
            "{\"title\": str, \"description\": str, \"estimated_minutes\": int, \"priority\": int, \"tags\": [str], \"energy_cost\": int}. "
            "Use 3 to max_subtasks items when possible. "
            "Estimated minutes must be between 5 and 240. "
            "Priority must be between 1 and 5. "
            "Energy cost must be between 1 and 5. "
            "Keep each subtask concrete and short. "
            "Do not include markdown or explanations.\n"
            f"Input: {json.dumps(payload, ensure_ascii=False)}"
        )

    def _build_nl_task_prompt(self, cfg: SlmConfig, text: str, default_tags: list[str]) -> str:
        payload = {
            "display_name": cfg.display_name,
            "current_goals": cfg.current_goals,
            "default_tags": default_tags,
            "text": text,
        }
        return (
            "You are extracting actionable productivity task drafts from natural language. "
            "You are not a therapist, clinician, or medical advisor. "
            "Return JSON only. "
            "Return a JSON array of objects. "
            "Each object may contain these keys: "
            "{\"title\": str, \"description\": str, \"estimated_minutes\": int, \"priority\": int, \"tags\": [str], \"energy_cost\": int}. "
            "Split broad requests into separate concrete tasks. "
            "Ignore non-actionable filler. "
            "Do not include markdown or explanations.\n"
            f"Input: {json.dumps(payload, ensure_ascii=False)}"
        )

    def _build_weekly_review_prompt(self, stats: CoachStats, cfg: SlmConfig) -> str:
        payload = {
            "display_name": cfg.display_name,
            "current_goals": cfg.current_goals,
            "week_start": stats.week_start.isoformat(),
            "week_end": stats.week_end.isoformat(),
            "total_sessions": stats.total_sessions,
            "total_minutes": stats.total_minutes,
            "avg_focus_score": stats.avg_focus_score,
            "top_focus_hours": stats.top_focus_hours,
            "top_tags": stats.top_tags,
            "completed_tasks": stats.completed_tasks,
            "created_tasks": stats.created_tasks,
            "distraction_events": stats.distraction_events,
            "absent_seconds": stats.absent_seconds,
        }
        return (
            "You are a supportive focus coach. "
            "You are not a therapist, clinician, or diagnostic tool. "
            "Write a weekly review using non-judgmental language, short paragraphs, and 2-3 concrete experiments for next week. "
            "Avoid mental health diagnosis or medical framing.\n"
            f"Stats: {json.dumps(payload, ensure_ascii=False)}"
        )

    def _run_llm(self, prompt: str, cfg: SlmConfig, is_weekly_review: bool = False, task_type: str = "standard") -> str:
        diagnostics = plan_execution(cfg, prompt, self._benchmark_store)
        self._last_diagnostics = diagnostics
        self._last_plan = diagnostics.plan

        if cfg.backend == "onnx_runtime":
            raw = self._run_onnx_runtime(prompt, cfg, diagnostics.plan)
        else:
            # Optimization for low end systems
            raw = self._run_llama_cpp(
                prompt,
                cfg,
                diagnostics.plan,
                is_weekly_review=is_weekly_review,
                task_type=task_type,
            )

        return self._clean_thinking_tags(raw)

    def _clean_thinking_tags(self, text: str) -> str:
        if not text:
            return ""
        
        import re
        # Strip [Start thinking]...[End thinking] (case-insensitive, dotall)
        text = re.sub(r"\[Start thinking\].*?\[End thinking\]", "", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"\[thinking\].*?\[End thinking\]", "", text, flags=re.IGNORECASE | re.DOTALL)
        
        # Strip <thought>...</thought> (case-insensitive, dotall)
        text = re.sub(r"<thought>.*?</thought>", "", text, flags=re.IGNORECASE | re.DOTALL)
        # Strip <thinking>...</thinking>
        text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.IGNORECASE | re.DOTALL)
        
        # Strip any open-ended [Start thinking], [thinking], <thought> or <thinking> to the end of the text
        text = re.sub(r"\[Start thinking\].*", "", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"\[thinking\].*", "", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<thought>.*", "", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<thinking>.*", "", text, flags=re.IGNORECASE | re.DOTALL)
        
        # Strip speed stats like "[ Prompt: 8.8 t/s | Generation: 5.9 t/s ]" or similar metrics
        text = re.sub(r"\[\s*Prompt:.*?Generation:.*?\]", "", text, flags=re.IGNORECASE | re.DOTALL)
        
        return text.strip()

    def _run_onnx_runtime(self, prompt: str, cfg: SlmConfig, plan: RuntimeExecutionPlan) -> str:
        """ONNX backend is not yet implemented. Returns empty string.

        The ONNX backend requires a tokenizer + autoregressive generation loop
        that is not yet shipped. Users should configure backend=llama_cpp instead.
        This stub is retained so the backend selector in settings can exist without
        crashing, but it will never produce output.
        """
        _ = (prompt, cfg, plan)
        return ""

    def _run_llama_cpp(
        self,
        prompt: str,
        cfg: SlmConfig,
        plan: RuntimeExecutionPlan,
        is_weekly_review: bool = False,
        task_type: str = "standard",
    ) -> str:
        if not cfg.model_path.exists():
            return ""

        binary = (
            resolve_llama_binary("llama-cli")
            or resolve_llama_binary("main")
            or resolve_llama_binary("llama")
        )
        if not binary:
            return ""

        # Optimization for low end systems
        from .model_catalog import detect_entry_for_path
        entry = detect_entry_for_path(cfg.model_path)
        is_thinking_model = entry is not None and "thinking" in entry.tags

        is_low_end = False
        try:
            import psutil
            is_low_end = (psutil.cpu_count(logical=True) or 4) <= 4
        except Exception:
            pass

        if is_weekly_review or task_type == "weekly_review":
            context_size = 3072 if is_thinking_model else 2048
            max_tokens = 1536 if is_thinking_model else 1024
            timeout = 300 if is_thinking_model else 180
            reasoning_budget = 384 if is_low_end else 768
        elif task_type in ("decomposition", "scheduling"):
            context_size = 1536 if is_thinking_model else 512
            max_tokens = 768 if is_thinking_model else cfg.max_tokens
            timeout = 180 if is_thinking_model else max(30, int(cfg.planner_timeout_ms / 1000) + 120)
            reasoning_budget = 64 if is_low_end else 256
        elif task_type == "classification":
            context_size = 1536 if is_thinking_model else 512
            max_tokens = 768 if is_thinking_model else cfg.max_tokens
            timeout = 180 if is_thinking_model else max(30, int(cfg.planner_timeout_ms / 1000) + 120)
            reasoning_budget = 0
        else:
            context_size = 1536 if is_thinking_model else 512
            max_tokens = 768 if is_thinking_model else cfg.max_tokens
            timeout = 180 if is_thinking_model else max(30, int(cfg.planner_timeout_ms / 1000) + 120)
            reasoning_budget = 64 if is_low_end else 128

        cmd = [binary, "-m", str(cfg.model_path), "-p", prompt, "-n", str(max_tokens), "-c", str(context_size)]

        # Check if the binary supports newer flags to avoid breaking older llama/main binaries
        binary_key = str(binary)
        if binary_key not in self._supported_flags_cache:
            try:
                res = subprocess.run([binary, "-h"], capture_output=True, text=True, timeout=5)
                help_text = (res.stdout or "") + (res.stderr or "")
                self._supported_flags_cache[binary_key] = {
                    "no-cnv": "-no-cnv" in help_text or "--no-conversation" in help_text,
                    "no-display-prompt": "--no-display-prompt" in help_text,
                }
            except Exception:
                self._supported_flags_cache[binary_key] = {
                    "no-cnv": False,
                    "no-display-prompt": False,
                }

        flags = self._supported_flags_cache[binary_key]
        if flags.get("no-cnv"):
            cmd.append("-no-cnv")
        if flags.get("no-display-prompt"):
            cmd.append("--no-display-prompt")

        if is_thinking_model:
            cmd.extend(["--reasoning-budget", str(reasoning_budget)])
            cmd.extend(["--reasoning-budget-message", "\n[Reasoning budget reached, continuing with the final output]\n"])

        if plan.cpu_threads is not None:
            cmd.extend(["-t", str(plan.cpu_threads)])

        if plan.llama_gpu_layers != 0:
            cmd.extend(["-ngl", str(plan.llama_gpu_layers)])

        popen_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
        }
        if not sys.platform.startswith("win"):
            popen_kwargs["preexec_fn"] = os.setsid

        process = None
        try:
            process = subprocess.Popen(cmd, **popen_kwargs)
            self._register_process(process)

            stdout_queue = queue.Queue()
            stderr_queue = queue.Queue()

            stdout_reader = AsynchronousFileReader(process.stdout, stdout_queue)
            stdout_reader.start()
            stderr_reader = AsynchronousFileReader(process.stderr, stderr_queue)
            stderr_reader.start()

            stdout_chunks = []
            stderr_chunks = []

            t_start = time.perf_counter()
            cancelled = False

            while process.poll() is None:
                current_thread = QThread.currentThread()
                current_thread_id = threading.get_ident()
                
                from app.services.slm.slm_async import _CANCELLED_THREADS, _CANCELLED_THREADS_LOCK
                is_cancelled_by_registry = False
                with _CANCELLED_THREADS_LOCK:
                    if current_thread_id in _CANCELLED_THREADS:
                        is_cancelled_by_registry = True

                if getattr(current_thread, "_cancelled", False) or is_cancelled_by_registry:
                    cancelled = True
                    break

                if time.perf_counter() - t_start > timeout:
                    break

                # Drain reader queues
                while not stdout_queue.empty():
                    try:
                        stdout_chunks.append(stdout_queue.get_nowait())
                    except queue.Empty:
                        break
                while not stderr_queue.empty():
                    try:
                        stderr_chunks.append(stderr_queue.get_nowait())
                    except queue.Empty:
                        break

                time.sleep(0.05)

            # Final drain of remaining output
            while not stdout_queue.empty():
                try:
                    stdout_chunks.append(stdout_queue.get_nowait())
                except queue.Empty:
                    break
            while not stderr_queue.empty():
                try:
                    stderr_chunks.append(stderr_queue.get_nowait())
                except queue.Empty:
                    break

            if cancelled or process.poll() is None:
                self._terminate_process(process)
                return ""

            if process.returncode != 0:
                return ""

            return "".join(stdout_chunks).strip()

        except Exception:
            if process:
                self._terminate_process(process)
            return ""
        finally:
            if process:
                self._unregister_process(process)

    def _parse_decomposition(self, raw: str) -> list[DecomposedTask]:
        text = raw.strip()
        if not text:
            return []
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last != -1 and last > first:
            text = text[first : last + 1]
        try:
            payload = json.loads(text)
        except Exception:
            return []
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []
        parsed: list[DecomposedTask] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            description = str(item.get("description", "")).strip()
            try:
                minutes = int(item.get("estimated_minutes", 30) or 30)
            except Exception:
                minutes = 30
            minutes = max(5, min(minutes, 240))
            try:
                energy = int(item.get("energy_cost", 3) or 3)
            except Exception:
                energy = 3
            energy = max(1, min(energy, 5))
            raw_tags = item.get("tags") or []
            tags = [str(tag) for tag in raw_tags] if isinstance(raw_tags, list) else []
            parsed.append(
                DecomposedTask(
                    title=title,
                    description=description,
                    estimated_minutes=minutes,
                    tags=tags,
                    energy_cost=energy,
                )
            )
        return parsed