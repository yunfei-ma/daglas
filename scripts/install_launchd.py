from __future__ import annotations

import logging
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

HEARTBEAT_LABEL = "com.daglas.heartbeat"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"  # where the .plist lives for launchd
LAUNCH_DAEMONS_DIR = Path("/Library/LaunchDaemons")
LOG_DIR = Path.home() / "Library" / "Logs" / "daglas"
INSTALL_DIR = Path.home() / "daglas"  # where the project code is copied to
SOURCE_ROOT = Path(__file__).resolve().parent.parent

CONFIG_KEYS_TO_REWRITE = {"data_dir", "prompts_dir"}
MAGIC_STRING_TARGET = "target"  # sentinel: "orient" = source/dev, "target" = installed copy. Logged on startup to verify which config.yaml is loaded.

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


def _check_macos() -> None:
    if sys.platform != "darwin":
        print("ERROR: launchd is only available on macOS.")
        sys.exit(1)


def _copy_project() -> None:
    if INSTALL_DIR.exists():
        shutil.rmtree(INSTALL_DIR)
    ignore = shutil.ignore_patterns(
        ".git", "__pycache__", ".ruff_cache", ".pytest_cache", "*.egg-info"
    )
    shutil.copytree(SOURCE_ROOT, INSTALL_DIR, ignore=ignore)
    logger.info("Copied project to %s", INSTALL_DIR)


def _rewrite_config() -> None:
    import yaml

    config_path = INSTALL_DIR / "config.yaml"
    if not config_path.is_file():
        return
    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}
    for key in CONFIG_KEYS_TO_REWRITE:
        if key in raw:
            raw[key] = key.replace("_dir", "")
    raw["magic_string"] = MAGIC_STRING_TARGET
    with open(config_path, "w") as f:
        yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)
    logger.info("Rewrote config.yaml with relative paths")


def _ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _build_heartbeat_plist(python_bin: Path) -> dict:
    return {
        "Label": HEARTBEAT_LABEL,
        "ProgramArguments": [str(python_bin), "-m", "daglas.run"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(INSTALL_DIR),
        "StandardOutPath": str(LOG_DIR / "heartbeat.log"),
        "StandardErrorPath": str(LOG_DIR / "heartbeat.err"),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    }


def _unload_user_agents() -> None:
    for label in ("com.daglas.lessonGenerator", "com.daglas.runner", HEARTBEAT_LABEL):
        plist = LAUNCH_AGENTS_DIR / f"{label}.plist"
        if plist.is_file():
            subprocess.run(
                ["launchctl", "unload", str(plist)],
                check=False,
                capture_output=True,
            )
            plist.unlink()
            logger.info("Removed user agent %s", label)


def install() -> None:
    _check_macos()

    print("Resolving Python...")
    python_bin = _resolve_python()
    print(f"  Using {python_bin}")

    print(f"Copying project to {INSTALL_DIR}...")
    _copy_project()
    _rewrite_config()

    _ensure_dirs()
    _unload_user_agents()

    plist = _build_heartbeat_plist(python_bin)
    plist_path = INSTALL_DIR / f"{HEARTBEAT_LABEL}.plist"
    with open(plist_path, "wb") as f:
        plistlib.dump(plist, f)
    logger.info("Wrote %s", plist_path)

    agent_dest = LAUNCH_AGENTS_DIR / f"{HEARTBEAT_LABEL}.plist"
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(plist_path, agent_dest)
    logger.info("Copied to %s", agent_dest)

    uid = os.getuid()
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(agent_dest)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: launchctl bootstrap failed: {result.stderr.strip()}")
        sys.exit(1)

    print("\nInstalled:")
    print(f"  {HEARTBEAT_LABEL}  — LaunchAgent, persistent (KeepAlive + RunAtLoad)")
    print(f"  Project: {INSTALL_DIR}")
    print(f"  Logs:    {LOG_DIR}")


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
