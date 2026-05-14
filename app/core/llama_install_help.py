# llama_install_help.py
from __future__ import annotations

from dataclasses import dataclass
from PySide6.QtCore import QSysInfo


@dataclass(slots=True)
class InstallGuide:
    title: str
    summary: str
    commands: list[str]
    notes: list[str]


class LlamaInstallGuideFactory:
    @staticmethod
    def build() -> InstallGuide:
        product = QSysInfo.productType().lower()
        pretty = QSysInfo.prettyProductName()
        arch = QSysInfo.currentCpuArchitecture()

        if product == "windows":
            return InstallGuide(
                title=f"Windows setup — {pretty}",
                summary="Install Visual Studio 2022 with the C++ desktop workload, then build llama.cpp with CMake.",
                commands=[
                    "git clone https://github.com/ggml-org/llama.cpp",
                    "cd llama.cpp",
                    "cmake -B build",
                    "cmake --build build --config Release",
                ],
                notes=[
                    "Use a Developer Command Prompt or Developer PowerShell for VS 2022.",
                    "Install these VS components: Desktop development with C++, CMake tools, Git for Windows, C++ Clang Compiler for Windows.",
                    "Typical output path is build/bin/Release.",
                ],
            )

        if product == "macos":
            return InstallGuide(
                title=f"macOS setup — {pretty}",
                summary="Install Xcode command line tools, clone llama.cpp, then build with CMake. Metal is enabled by default on macOS.",
                commands=[
                    "xcode-select --install",
                    "git clone https://github.com/ggml-org/llama.cpp",
                    "cd llama.cpp",
                    "cmake -B build",
                    "cmake --build build --config Release",
                ],
                notes=[
                    f"Detected CPU architecture: {arch}.",
                    "Metal support is enabled by default on macOS builds.",
                    "To force CPU-only build behavior, compile with -DGGML_METAL=OFF.",
                ],
            )

        if product in {"ubuntu", "neon", "pop"}:
            return InstallGuide(
                title=f"Ubuntu-family setup — {pretty}",
                summary="Install compiler and CMake packages with apt, then clone and build llama.cpp.",
                commands=[
                    "sudo apt update",
                    "sudo apt install -y build-essential cmake git libcurl4-openssl-dev",
                    "git clone https://github.com/ggml-org/llama.cpp",
                    "cd llama.cpp",
                    "cmake -B build",
                    "cmake --build build --config Release -j $(nproc)",
                ],
                notes=[
                    "This is the clean default CPU build.",
                    "libcurl development headers are needed when curl support is enabled.",
                ],
            )

        if product == "debian":
            return InstallGuide(
                title=f"Debian setup — {pretty}",
                summary="Install Debian build essentials, CMake, Git, and libcurl development headers, then build llama.cpp.",
                commands=[
                    "sudo apt update",
                    "sudo apt install -y build-essential cmake git libcurl4-openssl-dev",
                    "git clone https://github.com/ggml-org/llama.cpp",
                    "cd llama.cpp",
                    "cmake -B build",
                    "cmake --build build --config Release -j $(nproc)",
                ],
                notes=[
                    "Debian build-essential provides the standard build toolchain.",
                ],
            )

        if product in {"fedora", "rhel", "centos", "rocky", "almalinux"}:
            return InstallGuide(
                title=f"Fedora/RHEL-family setup — {pretty}",
                summary="Install GCC, CMake, Git, and libcurl development headers with dnf, then build llama.cpp.",
                commands=[
                    "sudo dnf install -y git cmake gcc gcc-c++ libcurl-devel",
                    "git clone https://github.com/ggml-org/llama.cpp",
                    "cd llama.cpp",
                    "cmake -B build",
                    "cmake --build build --config Release -j $(nproc)",
                ],
                notes=[
                    "Fedora-family systems use libcurl-devel for curl headers.",
                ],
            )

        if product in {"arch", "manjaro"}:
            return InstallGuide(
                title=f"Arch-family setup — {pretty}",
                summary="Install base-devel, CMake, Git, and curl with pacman, then build llama.cpp.",
                commands=[
                    "sudo pacman -Syu --needed base-devel cmake git curl",
                    "git clone https://github.com/ggml-org/llama.cpp",
                    "cd llama.cpp",
                    "cmake -B build",
                    "cmake --build build --config Release -j $(nproc)",
                ],
                notes=[
                    "Arch-family systems generally use base-devel for the standard toolchain.",
                    "Arch packages curl with libcurl headers.",
                ],
            )

        if product in {"opensuse-leap", "opensuse-tumbleweed", "opensuse"}:
            return InstallGuide(
                title=f"openSUSE setup — {pretty}",
                summary="Install GCC C++, CMake, and Git with zypper, then build llama.cpp.",
                commands=[
                    "sudo zypper install -y git gcc-c++ make cmake",
                    "git clone https://github.com/ggml-org/llama.cpp",
                    "cd llama.cpp",
                    "cmake -B build",
                    "cmake --build build --config Release -j $(nproc)",
                ],
                notes=[
                    "openSUSE packaging commonly exposes gcc-c++ and cmake directly via zypper.",
                ],
            )

        return InstallGuide(
            title=f"Generic CMake setup — {pretty}",
            summary="Install Git, a C/C++ compiler toolchain, and CMake, then clone and build llama.cpp.",
            commands=[
                "git clone https://github.com/ggml-org/llama.cpp",
                "cd llama.cpp",
                "cmake -B build",
                "cmake --build build --config Release",
            ],
            notes=[
                f"Detected platform: {pretty}",
                f"Detected architecture: {arch}",
            ],
        )