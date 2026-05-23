from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import math
import psutil

from .benchmark_store import BenchmarkStore
from .model_catalog import ModelEntry, get_safe_models
from .models import CpuSnapshot, GpuSnapshot, PlannerDiagnostics, RuntimeExecutionPlan, SlmConfig, resolve_llama_binary

# ---------------------------------------------------------------------------
# Key constant: ABTE should not consume more than 40% of available CPU so
# that the rest of the system remains usable. We apply this fraction when
# evaluating whether a model can run reliably within the 120 s timeout.
# ---------------------------------------------------------------------------
ABTE_CPU_BUDGET_FRACTION = 0.40


def sample_cpu() -> CpuSnapshot:
    vm = psutil.virtual_memory()
    return CpuSnapshot(
        utilization_percent=float(psutil.cpu_percent(interval=0.15)),
        free_memory_mb=int(vm.available // (1024 * 1024)),
        logical_cores=int(psutil.cpu_count(logical=True) or 1),
    )


def sample_gpu() -> GpuSnapshot:
    try:
        import pynvml  # type: ignore
        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="ignore")
            return GpuSnapshot(
                available=True,
                name=str(name),
                utilization_percent=float(util.gpu),
                free_memory_mb=int(mem.free // (1024 * 1024)),
                total_memory_mb=int(mem.total // (1024 * 1024)),
                provider="cuda",
            )
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        pass
    return GpuSnapshot(
        available=False, name=None, utilization_percent=None,
        free_memory_mb=None, total_memory_mb=None, provider=None,
    )


def detect_onnx_provider() -> str | None:
    try:
        import onnxruntime as ort  # type: ignore
        providers = set(ort.get_available_providers())
        if "CUDAExecutionProvider" in providers:
            return "CUDAExecutionProvider"
        if "DmlExecutionProvider" in providers:
            return "DmlExecutionProvider"
        if "CPUExecutionProvider" in providers:
            return "CPUExecutionProvider"
    except Exception:
        return None
    return None


def estimate_model_size_mb(model_path: Path) -> int:
    try:
        return int(model_path.stat().st_size // (1024 * 1024))
    except Exception:
        return 0


def estimate_prompt_complexity(prompt: str, max_tokens: int) -> float:
    return float(max(1, len(prompt) // 4) + max_tokens)


def prompt_bucket_for(prompt: str, max_tokens: int) -> str:
    total = estimate_prompt_complexity(prompt, max_tokens)
    if total <= 512:
        return "small"
    if total <= 1536:
        return "medium"
    return "large"


def _cpu_raw_score(cpu: CpuSnapshot) -> float:
    """cores × idle_fraction — raw available compute."""
    idle = max(0.05, (100.0 - cpu.utilization_percent) / 100.0)
    return round(cpu.logical_cores * idle, 2)


def _primary_model_min_effective(model_mb: int) -> float:
    """Minimum *effective* CPU score (after budget fraction) for a model tier."""
    if model_mb < 1200:
        return 0.8   # lightweight: e.g. LFM-1.2B
    if model_mb < 2000:
        return 1.5   # medium
    return 2.5       # heavy: e.g. phi-mini


# ---------------------------------------------------------------------------
# System feasibility
# ---------------------------------------------------------------------------

@dataclass
class SystemFeasibilityResult:
    primary_model_safe: bool
    summary: str
    ram_ok: bool
    cpu_speed_ok: bool
    binary_found: bool
    model_exists: bool
    gpu_available: bool
    gpu_vram_ok: bool
    free_ram_mb: int
    logical_cores: int
    cpu_raw_score: float
    cpu_effective_score: float   # cpu_raw_score * ABTE_CPU_BUDGET_FRACTION
    gpu_free_mb: int
    safe_alternatives: list[ModelEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def assess_feasibility(cfg: SlmConfig) -> SystemFeasibilityResult:
    """Pre-benchmark system capability check using the 40% CPU budget model."""
    import shutil

    cpu = sample_cpu()
    gpu = sample_gpu()

    free_ram_mb = cpu.free_memory_mb
    cpu_raw = _cpu_raw_score(cpu)
    cpu_effective = round(cpu_raw * ABTE_CPU_BUDGET_FRACTION, 2)
    gpu_free_mb = int(gpu.free_memory_mb or 0)

    # Estimate model size from file; fall back to name-based heuristic
    model_mb = estimate_model_size_mb(cfg.model_path)
    if model_mb == 0:
        path_lower = str(cfg.model_path).lower()
        if "phi" in path_lower and ("mini" in path_lower or "3.8" in path_lower):
            model_mb = 2200
        elif "1.2b" in path_lower or "lfm" in path_lower:
            model_mb = 900
        else:
            model_mb = 2200  # conservative

    ram_required_mb = max(model_mb + 400, int(model_mb * 1.2))
    ram_ok = free_ram_mb >= ram_required_mb

    primary_min_eff = _primary_model_min_effective(model_mb)
    cpu_speed_ok = cpu_effective >= primary_min_eff

    binary_found = bool(
        resolve_llama_binary("llama-cli") or resolve_llama_binary("main") or resolve_llama_binary("llama")
    )
    try:
        model_exists = cfg.model_path.exists() and cfg.model_path.stat().st_size > 0
    except Exception:
        model_exists = False

    gpu_available = gpu.available
    gpu_vram_ok = gpu_available and gpu_free_mb >= (model_mb + 512)

    warnings: list[str] = []
    if not binary_found:
        warnings.append("llama-cli binary not found on PATH. Install llama.cpp first.")
    if not model_exists:
        warnings.append(f"Model file not found at {cfg.model_path}. Download it first.")
    if not ram_ok:
        warnings.append(
            f"Low free RAM: {free_ram_mb} MB available, ~{ram_required_mb} MB needed."
        )
    if not cpu_speed_ok and not gpu_vram_ok:
        warnings.append(
            f"CPU budget ({cpu_effective:.1f} = {cpu_raw:.1f} × 40%) is below "
            f"the {primary_min_eff:.1f} threshold for this model — "
            "likely to hit the 120 s timeout while other apps are running."
        )

    primary_model_safe = binary_found and model_exists and ((ram_ok and cpu_speed_ok) or gpu_vram_ok)

    safe_alts = get_safe_models(cpu_effective, free_ram_mb, gpu_free_mb)

    parts = [
        f"Free RAM: {free_ram_mb} MB",
        f"CPU budget: {cpu_effective:.1f} ({cpu_raw:.1f} × 40%, {cpu.logical_cores} cores @ {cpu.utilization_percent:.0f}% load)",
    ]
    if gpu_available:
        parts.append(f"GPU: {gpu.name or 'detected'}, {gpu_free_mb} MB free VRAM")
    else:
        parts.append("GPU: not detected")

    status = "✓ Safe to benchmark" if primary_model_safe else "⚠ Not recommended"
    summary = f"{status} — {' | '.join(parts)}"

    return SystemFeasibilityResult(
        primary_model_safe=primary_model_safe,
        summary=summary,
        ram_ok=ram_ok,
        cpu_speed_ok=cpu_speed_ok,
        binary_found=binary_found,
        model_exists=model_exists,
        gpu_available=gpu_available,
        gpu_vram_ok=gpu_vram_ok,
        free_ram_mb=free_ram_mb,
        logical_cores=cpu.logical_cores,
        cpu_raw_score=cpu_raw,
        cpu_effective_score=cpu_effective,
        gpu_free_mb=gpu_free_mb,
        safe_alternatives=safe_alts,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Benchmark-adjusted planning (unchanged logic)
# ---------------------------------------------------------------------------

def _benchmark_adjusted_plan(
    heuristic_plan: RuntimeExecutionPlan,
    benchmark_summary,
    notes: list[str],
) -> tuple[RuntimeExecutionPlan, float, float]:
    if benchmark_summary is None or benchmark_summary.best_target is None:
        notes.append("No matching benchmark summary found; using heuristic-only planning.")
        return heuristic_plan, 0.0, 1.0

    benchmark_weight = min(0.65, 0.2 + (benchmark_summary.record_count * 0.075))
    heuristic_weight = max(0.35, 1.0 - benchmark_weight)

    benchmark_latency = {
        "cpu": benchmark_summary.cpu_median_seconds,
        "gpu": benchmark_summary.gpu_median_seconds,
        "hybrid": benchmark_summary.hybrid_median_seconds,
    }
    best_target = benchmark_summary.best_target
    best_latency = benchmark_latency.get(best_target)
    if best_latency is None:
        notes.append("Benchmark summary is incomplete; keeping heuristic plan.")
        return heuristic_plan, 0.0, 1.0

    chosen_latency = (
        heuristic_plan.estimated_latency_seconds * heuristic_weight
        + best_latency * benchmark_weight
    )
    notes.append(
        f"Benchmark cache favored {best_target} with median {best_latency:.3f}s "
        f"over {benchmark_summary.record_count} successful runs."
    )
    return RuntimeExecutionPlan(
        target=best_target,
        backend=heuristic_plan.backend,
        estimated_latency_seconds=round(chosen_latency, 3),
        score=round(1.0 / max(chosen_latency, 0.001), 4),
        reason=f"{heuristic_plan.reason} Benchmark history shifted preference toward {best_target}.",
        llama_gpu_layers=heuristic_plan.llama_gpu_layers if best_target in {"gpu", "hybrid"} else 0,
        onnx_provider=heuristic_plan.onnx_provider,
        cpu_threads=heuristic_plan.cpu_threads,
    ), benchmark_weight, heuristic_weight


def plan_execution(
    cfg: SlmConfig, prompt: str, benchmark_store: BenchmarkStore | None = None
) -> PlannerDiagnostics:
    cpu = sample_cpu()
    gpu = sample_gpu()
    model_mb = max(estimate_model_size_mb(cfg.model_path), 1)
    token_work = estimate_prompt_complexity(prompt, cfg.max_tokens)
    notes: list[str] = []

    cpu_free_factor = max(0.05, (100.0 - cpu.utilization_percent) / 100.0)
    cpu_mem_factor = 1.0 if cpu.free_memory_mb > (model_mb + cfg.cpu_memory_reserve_mb) else 0.45
    # Optimization for low end systems
    if cpu.logical_cores <= 4:
        cpu_threads = cfg.cpu_threads or max(1, min(2, cpu.logical_cores // 2))
    else:
        cpu_threads = cfg.cpu_threads or max(1, min(int(cpu.logical_cores * ABTE_CPU_BUDGET_FRACTION), 4))
    cpu_power_factor = max(0.35, min(1.4, cpu_threads / 8.0))
    est_cpu_latency = (token_work / 140.0) * (1.0 / cpu_free_factor) * (1.0 / cpu_power_factor) * (1.0 / cpu_mem_factor)

    if cfg.backend == "onnx_runtime":
        provider = detect_onnx_provider()
        if provider in {"CUDAExecutionProvider", "DmlExecutionProvider"} and gpu.available:
            gpu_free_factor = max(0.05, (100.0 - float(gpu.utilization_percent or 100.0)) / 100.0)
            gpu_mem_fit = (gpu.free_memory_mb or 0) > max(int(model_mb * 0.7), 512) + cfg.gpu_memory_reserve_mb
            gpu_mem_factor = 1.0 if gpu_mem_fit else 0.35
            est_gpu_latency = (token_work / 420.0) * (1.0 / gpu_free_factor) * (1.0 / gpu_mem_factor)
            heuristic_plan = RuntimeExecutionPlan(
                target="gpu" if est_gpu_latency < est_cpu_latency * 1.15 else "cpu",
                backend=cfg.backend,
                estimated_latency_seconds=round(min(est_gpu_latency, est_cpu_latency), 3),
                score=round(1.0 / max(min(est_gpu_latency, est_cpu_latency), 0.001), 4),
                reason="ONNX planner compared available accelerator provider against CPU estimate.",
                llama_gpu_layers=0, onnx_provider=provider, cpu_threads=cpu_threads,
            )
        else:
            heuristic_plan = RuntimeExecutionPlan(
                target="cpu", backend=cfg.backend,
                estimated_latency_seconds=round(est_cpu_latency, 3),
                score=round(1.0 / max(est_cpu_latency, 0.001), 4),
                reason="Selected CPU because no suitable ONNX GPU execution provider or memory fit was detected.",
                llama_gpu_layers=0, onnx_provider=provider or "CPUExecutionProvider", cpu_threads=cpu_threads,
            )
    else:
        if not gpu.available or not cfg.prefer_gpu:
            heuristic_plan = RuntimeExecutionPlan(
                target="cpu", backend=cfg.backend,
                estimated_latency_seconds=round(est_cpu_latency, 3),
                score=round(1.0 / max(est_cpu_latency, 0.001), 4),
                reason="Selected CPU because GPU is unavailable or GPU preference is disabled.",
                llama_gpu_layers=0, onnx_provider=None, cpu_threads=cpu_threads,
            )
        else:
            gpu_free_factor = max(0.05, (100.0 - float(gpu.utilization_percent or 100.0)) / 100.0)
            gpu_free_mb = int(gpu.free_memory_mb or 0)
            reserve = cfg.gpu_memory_reserve_mb
            full_gpu_fit = gpu_free_mb > model_mb + reserve
            partial_gpu_fit = gpu_free_mb > max(512, int(model_mb * 0.2))

            if full_gpu_fit:
                est_gpu_latency = (token_work / 520.0) * (1.0 / gpu_free_factor)
                if est_gpu_latency < est_cpu_latency * 1.4:
                    heuristic_plan = RuntimeExecutionPlan(
                        target="gpu", backend=cfg.backend,
                        estimated_latency_seconds=round(est_gpu_latency, 3),
                        score=round(1.0 / max(est_gpu_latency, 0.001), 4),
                        reason="Selected full GPU offload because model fits in free VRAM and estimated latency is lower.",
                        llama_gpu_layers=-1, onnx_provider=None, cpu_threads=cpu_threads,
                    )
                else:
                    heuristic_plan = RuntimeExecutionPlan(
                        target="cpu", backend=cfg.backend,
                        estimated_latency_seconds=round(est_cpu_latency, 3),
                        score=round(1.0 / max(est_cpu_latency, 0.001), 4),
                        reason="CPU estimate remained competitive despite full VRAM fit.",
                        llama_gpu_layers=0, onnx_provider=None, cpu_threads=cpu_threads,
                    )
            elif partial_gpu_fit:
                ratio = max(0.15, min(0.85, (gpu_free_mb - reserve) / max(model_mb, 1)))
                gpu_layers = cfg.gpu_layers_override if cfg.gpu_layers_override is not None else max(8, int(math.ceil(40 * ratio)))
                est_hybrid_latency = (token_work / 260.0) * (1.0 / gpu_free_factor)
                if est_hybrid_latency < est_cpu_latency:
                    heuristic_plan = RuntimeExecutionPlan(
                        target="hybrid", backend=cfg.backend,
                        estimated_latency_seconds=round(est_hybrid_latency, 3),
                        score=round(1.0 / max(est_hybrid_latency, 0.001), 4),
                        reason="Selected hybrid offload because GPU is VRAM-limited but still faster than CPU-only.",
                        llama_gpu_layers=gpu_layers, onnx_provider=None, cpu_threads=cpu_threads,
                    )
                else:
                    heuristic_plan = RuntimeExecutionPlan(
                        target="cpu", backend=cfg.backend,
                        estimated_latency_seconds=round(est_cpu_latency, 3),
                        score=round(1.0 / max(est_cpu_latency, 0.001), 4),
                        reason="Hybrid estimate did not beat CPU after contention adjustment.",
                        llama_gpu_layers=0, onnx_provider=None, cpu_threads=cpu_threads,
                    )
            else:
                heuristic_plan = RuntimeExecutionPlan(
                    target="cpu", backend=cfg.backend,
                    estimated_latency_seconds=round(est_cpu_latency, 3),
                    score=round(1.0 / max(est_cpu_latency, 0.001), 4),
                    reason="Selected CPU because projected GPU benefit does not outweigh memory limits.",
                    llama_gpu_layers=0, onnx_provider=None, cpu_threads=cpu_threads,
                )

    summary = None
    benchmark_weight = 0.0
    heuristic_weight = 1.0
    final_plan = heuristic_plan

    if benchmark_store is not None:
        bucket = prompt_bucket_for(prompt, cfg.max_tokens)
        summary = benchmark_store.summarize(
            model_path=str(cfg.model_path), backend=cfg.backend, prompt_bucket=bucket,
        )
        final_plan, benchmark_weight, heuristic_weight = _benchmark_adjusted_plan(
            heuristic_plan, summary, notes,
        )

    return PlannerDiagnostics(
        plan=final_plan, benchmark_summary=summary,
        benchmark_weight=benchmark_weight, heuristic_weight=heuristic_weight, notes=notes,
    )