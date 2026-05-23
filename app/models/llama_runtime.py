# llama_runtime.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil

from PySide6.QtCore import QSysInfo

from app.services.slm.models import resolve_llama_binary


@dataclass(slots=True)
class LlamaRuntimeStatus:
    found: bool
    executable: str | None
    executable_name: str | None
    search_paths_checked: list[str]
    product_type: str
    product_version: str
    pretty_product_name: str
    cpu_arch: str


class LlamaRuntimeDetector:
    EXECUTABLE_NAMES = [
        "llama-cli",
        "llama-server",
        "llama-bench",
        "llama-simple",
        "main",
    ]

    @classmethod
    def detect(cls) -> LlamaRuntimeStatus:
        checked: list[str] = []

        for name in cls.EXECUTABLE_NAMES:
            path = resolve_llama_binary(name)
            checked.append(f"PATH::{name}")
            if path:
                return LlamaRuntimeStatus(
                    found=True,
                    executable=path,
                    executable_name=name,
                    search_paths_checked=checked,
                    product_type=QSysInfo.productType(),
                    product_version=QSysInfo.productVersion(),
                    pretty_product_name=QSysInfo.prettyProductName(),
                    cpu_arch=QSysInfo.currentCpuArchitecture(),
                )

        candidates: list[Path] = []
        home = Path.home()
        cwd = Path.cwd()

        for base in [cwd, home, home / "dev", home / "src", home / "code"]:
            for repo_name in ["llama.cpp", "llama-cpp"]:
                repo = base / repo_name
                candidates.extend(
                    [
                        repo / "build" / "bin",
                        repo / "build" / "bin" / "Release",
                        repo / "bin",
                    ]
                )

        for folder in candidates:
            if not folder.exists():
                checked.append(str(folder))
                continue
            for name in cls.EXECUTABLE_NAMES:
                exe = folder / name
                exe_win = folder / f"{name}.exe"
                checked.append(str(exe))
                checked.append(str(exe_win))
                if exe.exists():
                    return LlamaRuntimeStatus(
                        found=True,
                        executable=str(exe),
                        executable_name=name,
                        search_paths_checked=checked,
                        product_type=QSysInfo.productType(),
                        product_version=QSysInfo.productVersion(),
                        pretty_product_name=QSysInfo.prettyProductName(),
                        cpu_arch=QSysInfo.currentCpuArchitecture(),
                    )
                if exe_win.exists():
                    return LlamaRuntimeStatus(
                        found=True,
                        executable=str(exe_win),
                        executable_name=f"{name}.exe",
                        search_paths_checked=checked,
                        product_type=QSysInfo.productType(),
                        product_version=QSysInfo.productVersion(),
                        pretty_product_name=QSysInfo.prettyProductName(),
                        cpu_arch=QSysInfo.currentCpuArchitecture(),
                    )

        return LlamaRuntimeStatus(
            found=False,
            executable=None,
            executable_name=None,
            search_paths_checked=checked,
            product_type=QSysInfo.productType(),
            product_version=QSysInfo.productVersion(),
            pretty_product_name=QSysInfo.prettyProductName(),
            cpu_arch=QSysInfo.currentCpuArchitecture(),
        )