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

HEARTBEAT_LABEL = "com.daglas.heartbeat"
HEARTBEAT_PLIST = LAUNCH_AGENTS_DIR / f"{HEARTBEAT_LABEL}.plist"

logger = logging.getLogger("install_launchd")


def _resolve_python() -> Path:
    candidates = [Path(p) / "python3" for p in ["/opt/homebrew/bin", "/usr/local/bin"]]
    for candidate in candidates:
        if candidate.is_file():
            ver = subprocess.run(
                [
                    str(candidate),
                    "-c",
                    "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            version = ver.stdout.strip()
            major, minor = (int(v) for v in version.split("."))
            if major > 3 or (major == 3 and minor >= 10):
                result = subprocess.run(
                    [str(candidate), "-c", "import daglas"],
                    capture_output=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    return candidate.resolve()
    resolved = Path(sys.executable).resolve()
    ver = subprocess.run(
        [
            str(resolved),
            "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    version = ver.stdout.strip()
    major, minor = (int(v) for v in version.split("."))
    if major < 3 or (major == 3 and minor < 10):
        print(
            f"ERROR: Python {version} is too old. daglas requires >=3.10. "
            f"Install a newer Python via Homebrew and re-run this script."
        )
        sys.exit(1)
    return resolved


PYTHON_BIN = _resolve_python()


def _check_macos() -> None:
    if sys.platform != "darwin":
        print("ERROR: launchd is only available on macOS.")
        sys.exit(1)


def _ensure_dirs() -> None:
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _build_heartbeat_plist() -> dict:
    return {
        "Label": HEARTBEAT_LABEL,
        "ProgramArguments": [str(PYTHON_BIN), "-m", "daglas.run"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(PROJECT_ROOT),
        "StandardOutPath": str(LOG_DIR / "heartbeat.log"),
        "StandardErrorPath": str(LOG_DIR / "heartbeat.err"),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    }


def _write_plist(path: Path, plist: dict) -> None:
    with open(path, "wb") as f:
        plistlib.dump(plist, f)
    logger.info("Wrote %s", path)


def _unload_if_exists(label: str) -> None:
    subprocess.run(
        ["launchctl", "unload", str(LAUNCH_AGENTS_DIR / f"{label}.plist")],
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

    _unload_if_exists("com.daglas.lessonGenerator")
    _unload_if_exists("com.daglas.runner")
    _unload_if_exists(HEARTBEAT_LABEL)

    plist = _build_heartbeat_plist()
    _write_plist(HEARTBEAT_PLIST, plist)
    _load_plist(HEARTBEAT_PLIST)

    print("Installed:")
    print(f"  {HEARTBEAT_LABEL}  — persistent (KeepAlive + RunAtLoad)")
    print(f"Logs: {LOG_DIR}")


if __name__ == "__main__":
    level = getattr(
        logging, os.environ.get("DAGLAS_LOG_LEVEL", "INFO").upper(), logging.INFO
    )
    if not isinstance(level, int):
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="[%(name)s] %(message)s",
        stream=sys.stderr,
    )
    install()
