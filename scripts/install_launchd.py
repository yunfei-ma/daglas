from __future__ import annotations

import logging
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LOG_DIR = Path.home() / "Library" / "Logs" / "daglas"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUN_PY = PROJECT_ROOT / "run.py"
PYTHON_BIN = Path(sys.executable).resolve()

OUTBOUND_LABEL = "com.daglas.outbound"
RUNNER_LABEL = "com.daglas.runner"

OUTBOUND_PLIST = LAUNCH_AGENTS_DIR / f"{OUTBOUND_LABEL}.plist"
RUNNER_PLIST = LAUNCH_AGENTS_DIR / f"{RUNNER_LABEL}.plist"

logger = logging.getLogger("install_launchd")


def _check_macos() -> None:
    if sys.platform != "darwin":
        print("ERROR: launchd is only available on macOS.")
        sys.exit(1)


def _ensure_dirs() -> None:
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _build_outbound_plist() -> dict:
    return {
        "Label": OUTBOUND_LABEL,
        "ProgramArguments": [str(PYTHON_BIN), str(RUN_PY), "--interval"],
        "StartInterval": 1800,
        "WorkingDirectory": str(PROJECT_ROOT),
        "StandardOutPath": str(LOG_DIR / "outbound.log"),
        "StandardErrorPath": str(LOG_DIR / "outbound.err"),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    }


def _build_runner_plist() -> dict:
    return {
        "Label": RUNNER_LABEL,
        "ProgramArguments": [str(PYTHON_BIN), str(RUN_PY)],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(PROJECT_ROOT),
        "StandardOutPath": str(LOG_DIR / "runner.log"),
        "StandardErrorPath": str(LOG_DIR / "runner.err"),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    }


def _write_plist(path: Path, plist: dict) -> None:
    with open(path, "wb") as f:
        plistlib.dump(plist, f)
    logger.info("Wrote %s", path)


def _unload_if_exists(path: Path) -> None:
    if path.is_file():
        subprocess.run(
            ["launchctl", "unload", str(path)],
            check=False,
            capture_output=True,
        )


def _load_plist(path: Path) -> None:
    result = subprocess.run(
        ["launchctl", "load", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: launchctl load failed for {path.name}: {result.stderr.strip()}")
        sys.exit(1)
    logger.info("Loaded %s", path.name)


def install() -> None:
    _check_macos()
    _ensure_dirs()

    outbound = _build_outbound_plist()
    runner = _build_runner_plist()

    _write_plist(OUTBOUND_PLIST, outbound)
    _write_plist(RUNNER_PLIST, runner)

    _unload_if_exists(OUTBOUND_PLIST)
    _unload_if_exists(RUNNER_PLIST)

    _load_plist(OUTBOUND_PLIST)
    _load_plist(RUNNER_PLIST)

    print("Installed:")
    print(f"  {OUTBOUND_LABEL}  — fires every 30 min")
    print(f"  {RUNNER_LABEL}    — persistent (KeepAlive + RunAtLoad)")
    print(f"Logs: {LOG_DIR}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[%(name)s] %(message)s",
        stream=sys.stderr,
    )
    install()
