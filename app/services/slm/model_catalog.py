from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

ModelTier = Literal["lightweight", "standard"]


@dataclass
class ModelEntry:
    """Catalog entry for a known GGUF model."""
    model_id: str
    name: str
    description: str
    filename: str           # expected filename when saved to models dir
    download_url: str
    size_mb_approx: int     # approximate download size in MB
    ram_required_mb: int    # minimum free RAM needed (MB)
    # Minimum *effective* CPU score needed = raw_score * ABTE_CPU_BUDGET_FRACTION
    min_cpu_score_effective: float
    tier: ModelTier
    tags: list[str] = field(default_factory=list)


# Canonical list ordered by resource requirement (smallest first).
KNOWN_MODELS: list[ModelEntry] = [
    ModelEntry(
        model_id="lfm2_5_1b_thinking_q5",
        name="LFM-2.5 1.2B Thinking Q5_K_M",
        description=(
            "LiquidAI 1.2B thinking model. ~850 MB download. "
            "Reliable on any modern CPU with ≥1.5 GB free RAM. "
            "Best choice for low-resource or older systems."
        ),
        filename="LFM2.5-1.2B-Thinking-Q5_K_M.gguf",
        download_url=(
            "https://huggingface.co/LiquidAI/LFM2.5-1.2B-Thinking-GGUF/resolve/main/"
            "LFM2.5-1.2B-Thinking-Q5_K_M.gguf"
        ),
        size_mb_approx=850,
        ram_required_mb=1100,
        min_cpu_score_effective=0.8,
        tier="lightweight",
        tags=["lightweight", "thinking", "recommended-low-end"],
    ),
    ModelEntry(
        model_id="phi3_mini_4k_q4",
        name="Phi-3 Mini 4K Q4_K_M",
        description=(
            "Microsoft Phi-3 mini 3.8B model. ~2.2 GB download. "
            "Strong reasoning quality. Requires a modern multi-core CPU "
            "(≥6 effective score) or GPU with ≥3 GB VRAM."
        ),
        filename="Phi-3-mini-4k-instruct-q4.gguf",
        download_url=(
            "https://huggingface.co/microsoft/Phi-3-mini-4k-instruct-gguf/resolve/main/"
            "Phi-3-mini-4k-instruct-q4.gguf"
        ),
        size_mb_approx=2200,
        ram_required_mb=2800,
        min_cpu_score_effective=2.5,
        tier="standard",
        tags=["standard", "recommended-high-end"],
    ),
]


def get_safe_models(
    cpu_effective_score: float,
    free_ram_mb: int,
    gpu_free_mb: int = 0,
) -> list[ModelEntry]:
    """Return catalog entries that are feasible for this system.

    cpu_effective_score = raw_cpu_score * ABTE_CPU_BUDGET_FRACTION
    """
    safe: list[ModelEntry] = []
    for entry in KNOWN_MODELS:
        ram_ok = free_ram_mb >= entry.ram_required_mb
        cpu_ok = cpu_effective_score >= entry.min_cpu_score_effective
        gpu_ok = gpu_free_mb >= (entry.ram_required_mb + 256)
        if ram_ok and (cpu_ok or gpu_ok):
            safe.append(entry)
    return safe


def find_by_id(model_id: str) -> ModelEntry | None:
    for entry in KNOWN_MODELS:
        if entry.model_id == model_id:
            return entry
    return None


def detect_entry_for_path(model_path: Path) -> ModelEntry | None:
    """Try to match a path against the catalog by filename."""
    name = model_path.name.lower()
    for entry in KNOWN_MODELS:
        if entry.filename.lower() == name:
            return entry
    return None


def is_matching_model_file(filename: str, entry: ModelEntry) -> bool:
    fn_lower = filename.lower()
    
    # Exact case-insensitive match
    if fn_lower == entry.filename.lower():
        return True
        
    # ID-based fuzzy checks
    if entry.model_id == "phi3_mini_4k_q4":
        # Match if it contains phi3 / phi-3, mini, and q4
        has_phi3 = ("phi-3" in fn_lower) or ("phi3" in fn_lower)
        has_mini = "mini" in fn_lower
        has_q4 = "q4" in fn_lower
        if has_phi3 and has_mini and has_q4:
            return True
            
    if entry.model_id == "lfm2_5_1b_thinking_q5":
        # Match if it contains lfm / liquid, thinking, and q5
        has_lfm = ("lfm" in fn_lower) or ("liquid" in fn_lower)
        has_thinking = "thinking" in fn_lower
        has_q5 = "q5" in fn_lower
        if has_lfm and has_thinking and has_q5:
            return True
            
    return False


def find_downloaded_model(entry: ModelEntry, app_data_dir: Path) -> Path | None:
    """Find if a model is already downloaded in common cache directories or local paths."""
    # 1. Primary app-specific model path
    app_models_dir = app_data_dir / "models"
    if app_models_dir.exists():
        try:
            for item in app_models_dir.glob("*.gguf"):
                if is_matching_model_file(item.name, entry) and item.stat().st_size > 0:
                    return item
        except Exception:
            pass

    # If running inside unit tests, bypass scanning global system directories to remain hermetic
    import os
    if "PYTEST_CURRENT_TEST" in os.environ:
        return None

    # 2. Executable directory (for PyInstaller packaging)
    import sys
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).parent
    else:
        exe_dir = Path(__file__).resolve().parents[3]  # root of project
        
    for d in [exe_dir, exe_dir / "models"]:
        if d.exists():
            try:
                for item in d.glob("*.gguf"):
                    if is_matching_model_file(item.name, entry) and item.stat().st_size > 0:
                        return item
            except Exception:
                pass

    # 3. Common user cache locations (HF, LM Studio, downloads, and app local shares)
    home = Path.home()
    candidates: list[Path] = []
    
    # Hugging Face cache
    candidates.append(home / ".cache" / "huggingface" / "hub")
    
    # LM Studio cache
    candidates.append(home / ".cache" / "lm-studio" / "models")
    candidates.append(home / ".lmstudio" / "models")
    candidates.append(home / "AppData" / "Local" / "lm-studio" / "cache" / "models")
    
    # Standard Downloads folder
    candidates.append(home / "Downloads")

    # Local Share / AppData search paths for matching app directories (like zyroo or Zyro)
    if home.joinpath(".local", "share").exists():
        candidates.append(home / ".local" / "share")
    if home.joinpath("AppData", "Local").exists():
        candidates.append(home / "AppData" / "Local")
        candidates.append(home / "AppData" / "Roaming")

    for base in candidates:
        if not base.exists():
            continue
            
        # First, check if direct child matches
        try:
            for item in base.glob("*.gguf"):
                if is_matching_model_file(item.name, entry) and item.stat().st_size > 0:
                    return item
        except Exception:
            pass
            
        # Quick check for subfolders
        try:
            if "huggingface" in str(base).lower():
                # We limit scanning specifically to folders matching the model's pattern
                model_pattern = "*phi-3*" if "phi-3" in entry.filename.lower() or "phi3" in entry.filename.lower() else "*lfm*"
                for snap_dir in base.glob(f"models--*{model_pattern}*/snapshots/*"):
                    if snap_dir.is_dir():
                        for item in snap_dir.glob("*.gguf"):
                            if is_matching_model_file(item.name, entry) and item.stat().st_size > 0:
                                return item
            else:
                # Recursively search directories containing 'zyroo', 'zyro', 'abte', or 'lm-studio'
                if base == home / ".local" / "share" or "appdata" in str(base).lower():
                    for sub in base.glob("*"):
                        if sub.is_dir() and any(k in sub.name.lower() for k in ["zyro", "abte", "lm-studio"]):
                            for item in sub.glob("**/*.gguf"):
                                if is_matching_model_file(item.name, entry) and item.stat().st_size > 0:
                                    return item
                else:
                    for item in base.glob("**/*.gguf"):
                        if is_matching_model_file(item.name, entry) and item.stat().st_size > 0:
                            return item
        except Exception:
            pass

    return None

