#!/usr/bin/env python3
"""
check_deps.py
Run this from the subtitle_agent root directory to verify all dependencies
are installed in their respective venvs before starting the servers.

Usage:
    python check_deps.py
"""

import importlib
import platform
import subprocess
import sys
from pathlib import Path

ROOT    = Path(__file__).parent
WINDOWS = platform.system() == "Windows"

def venv_python(venv_name: str) -> Path:
    """Return the python executable path inside a venv, cross-platform."""
    if WINDOWS:
        return ROOT / venv_name / ".venv" / "Scripts" / "python.exe"
    return ROOT / venv_name / ".venv" / "bin" / "python"

# (venv_dir, [(import_name, pip_name), ...])
CHECKS = [
    ("transcribe", [
        ("whisper",    "openai-whisper"),
        ("torch",      "torch"),
        ("mcp",        "mcp[cli]"),
        ("starlette",  "starlette"),
        ("uvicorn",    "uvicorn"),
    ]),
    ("ass_bilingual", [
        ("mcp",        "mcp[cli]"),
        ("starlette",  "starlette"),
        ("uvicorn",    "uvicorn"),
    ]),
    ("ass_translate", [
        ("pysubs2",    "pysubs2"),
        ("mcp",        "mcp[cli]"),
        ("starlette",  "starlette"),
        ("uvicorn",    "uvicorn"),
        ("httpx",      "httpx"),
    ]),
    ("ass_burnin", [
        ("mcp",        "mcp[cli]"),
        ("starlette",  "starlette"),
        ("uvicorn",    "uvicorn"),
    ]),
]

# Translation backend check (ass_translate venv)
BACKEND_CHECKS = [
    ("google.generativeai", "google-generativeai", "gemini"),
    ("anthropic",           "anthropic",           "claude"),
    ("openai",              "openai",              "openai"),
]

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def check_import(python_bin: str, import_name: str) -> bool:
    result = subprocess.run(
        [python_bin, "-c", f"import {import_name}"],
        capture_output=True,
    )
    return result.returncode == 0

def main():
    all_ok = True
    print(f"\n{BOLD}subtitle_agent dependency check{RESET}\n")

    for venv_name, packages in CHECKS:
        py = venv_python(venv_name)
        pip_cmd = str(py).replace("python.exe","pip").replace("python","pip")

        print(f"{BOLD}[{venv_name}]{RESET}")

        if not py.exists():
            print(f"  {RED}✗ venv not found: {py}{RESET}")
            print(f"    → run: python -m venv {venv_name}/.venv && "
                  f"{pip_cmd} install -r {venv_name}/requirements.txt")
            all_ok = False
            print()
            continue

        for import_name, pip_name in packages:
            ok = check_import(str(py), import_name)
            if ok:
                print(f"  {GREEN}✓{RESET} {import_name}")
            else:
                print(f"  {RED}✗ {import_name}{RESET}  → pip install {pip_name}")
                all_ok = False

        print()

    # Check translation backends in ass_translate venv
    translate_python = venv_python("ass_translate")
    if translate_python.exists():
        print(f"{BOLD}[ass_translate — translation backends]{RESET}")
        any_backend = False
        for import_name, pip_name, label in BACKEND_CHECKS:
            ok = check_import(str(translate_python), import_name)
            status = f"{GREEN}✓ available{RESET}" if ok else f"{YELLOW}○ not installed{RESET}"
            print(f"  {status}  {label:8s}  (pip install {pip_name})")
            if ok:
                any_backend = True
        if not any_backend:
            print(f"  {RED}✗ No translation backend installed! "
                  f"Install at least one of the above.{RESET}")
            all_ok = False
        print()

    # Check ffmpeg
    print(f"{BOLD}[system]{RESET}")
    find_cmd = ["where", "ffmpeg"] if WINDOWS else ["which", "ffmpeg"]
    result = subprocess.run(find_cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"  {GREEN}✓{RESET} ffmpeg  ({result.stdout.strip()})")
    else:
        print(f"  {RED}✗ ffmpeg not found in PATH{RESET}  → sudo apt install ffmpeg")
        all_ok = False

    # Check httpx for subtitle_agent.py itself
    try:
        import httpx
        print(f"  {GREEN}✓{RESET} httpx (for subtitle_agent.py)")
    except ImportError:
        print(f"  {RED}✗ httpx not found{RESET}  → pip install httpx  (system Python)")
        all_ok = False

    print()
    if all_ok:
        print(f"{GREEN}{BOLD}All checks passed. Ready to run ./start_servers.sh{RESET}")
    else:
        print(f"{RED}{BOLD}Some dependencies are missing. Fix the above then re-run this script.{RESET}")

    return 0 if all_ok else 1

if __name__ == "__main__":
    sys.exit(main())
