from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal


BackEnd = Literal["llama_cpp", "onnx_runtime"]
ComputeTarget = Literal["cpu", "gpu", "hybrid"]


@dataclass(slots=True)
class SlmConfig:
    backend: BackEnd
    model_path: Path
    max_tokens: int
    coach_enabled: bool
    decomposition_enabled: bool
    display_name: str
    current_goals: str
    startup_completed: bool
    prefer_gpu: bool
    cpu_threads: int | None
    gpu_layers_override: int | None
    gpu_memory_reserve_mb: int
    cpu_memory_reserve_mb: int
    planner_timeout_ms: int


@dataclass(slots=True)
class DecomposedTask:
    title: str
    description: str = ""
    estimated_minutes: int = 30
    tags: list[str] | None = None
    energy_cost: int = 3

    def normalized_tags(self) -> list[str]:
        result: list[str] = []
        for tag in self.tags or []:
            value = str(tag).strip().lower().lstrip("#")
            if value and value not in result:
                result.append(value)
        return result


@dataclass(slots=True)
class CoachStats:
    week_start: date
    week_end: date
    total_sessions: int
    total_minutes: int
    avg_focus_score: float | None
    top_focus_hours: list[int]
    top_tags: list[str]
    completed_tasks: int
    created_tasks: int
    distraction_events: int
    absent_seconds: int


@dataclass(slots=True)
class CpuSnapshot:
    utilization_percent: float
    free_memory_mb: int
    logical_cores: int


@dataclass(slots=True)
class GpuSnapshot:
    available: bool
    name: str | None
    utilization_percent: float | None
    free_memory_mb: int | None
    total_memory_mb: int | None
    provider: str | None


@dataclass(slots=True)
class RuntimeExecutionPlan:
    target: ComputeTarget
    backend: BackEnd
    estimated_latency_seconds: float
    score: float
    reason: str
    llama_gpu_layers: int
    onnx_provider: str | None
    cpu_threads: int | None



@dataclass(slots=True)
class BenchmarkRecord:
    benchmark_id: str
    created_at: str
    model_path: str
    backend: BackEnd
    target: ComputeTarget
    prompt_bucket: str
    max_tokens: int
    cpu_utilization_percent: float | None
    cpu_free_memory_mb: int | None
    gpu_name: str | None
    gpu_utilization_percent: float | None
    gpu_free_memory_mb: int | None
    duration_seconds: float
    success: bool
    error: str | None = None


@dataclass(slots=True)
class BenchmarkSummary:
    best_target: ComputeTarget | None
    cpu_median_seconds: float | None
    gpu_median_seconds: float | None
    hybrid_median_seconds: float | None
    record_count: int


@dataclass(slots=True)
class PlannerDiagnostics:
    plan: RuntimeExecutionPlan
    benchmark_summary: BenchmarkSummary | None = None
    benchmark_weight: float = 0.0
    heuristic_weight: float = 1.0
    notes: list[str] = field(default_factory=list)


def resolve_llama_binary(name: str) -> str | None:
    import os
    import sys
    import shutil
    
    # Check bundled paths or executable dir if frozen
    search_dirs = []
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            search_dirs.append(str(Path(sys._MEIPASS)))
            search_dirs.append(str(Path(sys._MEIPASS) / "bin"))
            search_dirs.append(str(Path(sys._MEIPASS) / "app" / "bin"))
        exe_dir = Path(sys.executable).parent
        search_dirs.append(str(exe_dir))
        search_dirs.append(str(exe_dir / "bin"))
    else:
        script_dir = Path(__file__).resolve().parent.parent.parent.parent
        search_dirs.append(str(script_dir))
        search_dirs.append(str(script_dir / "bin"))
        search_dirs.append(str(script_dir / ".venv" / "bin"))
        if sys.platform.startswith("win"):
            search_dirs.append(str(script_dir / ".venv" / "Scripts"))

    search_path_str = os.pathsep.join(search_dirs) + os.pathsep + os.environ.get("PATH", "")
    try:
        return shutil.which(name, path=search_path_str)
    except TypeError:
        return shutil.which(name)