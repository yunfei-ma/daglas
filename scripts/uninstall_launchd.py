from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

HEARTBEAT_LABEL = "com.daglas.heartbeat"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LAUNCH_DAEMONS_DIR = Path("/Library/LaunchDaemons")
INSTALL_DIR = Path.home() / "daglas"

logger = logging.getLogger("uninstall_launchd")


def _check_macos() -> None:
    if sys.platform != "darwin":
        print("ERROR: launchd is only available on macOS.")
        sys.exit(1)


def _unload_agent(label: str) -> None:
    plist = LAUNCH_AGENTS_DIR / f"{label}.plist"
    if not plist.is_file():
        return
    uid = os.getuid()
    result = subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{label}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"WARN: launchctl bootout failed for {label}: {result.stderr.strip()}")
    else:
        logger.info("Unloaded agent %s", label)
    plist.unlink()
    logger.info("Removed %s", plist)


def _unload_daemon(label: str) -> None:
    plist = LAUNCH_DAEMONS_DIR / f"{label}.plist"
    if not plist.is_file():
        return
    subprocess.run(
        ["sudo", "launchctl", "bootout", f"system/{label}"],
        check=False,
        capture_output=True,
    )
    subprocess.run(
        ["sudo", "rm", str(plist)],
        check=False,
        capture_output=True,
    )
    logger.info("Removed daemon %s", plist)


def uninstall() -> None:
    _check_macos()

    for label in ("com.daglas.lessonGenerator", "com.daglas.runner", HEARTBEAT_LABEL):
        _unload_agent(label)

    _unload_daemon(HEARTBEAT_LABEL)

    if INSTALL_DIR.is_dir():
        shutil.rmtree(INSTALL_DIR)
        logger.info("Removed %s", INSTALL_DIR)

    print("Uninstalled daglas heartbeat agent.")


if __name__ == "__main__":
    level = getattr(
        logging, __import__("os").environ.get("DAGLAS_LOG_LEVEL", "INFO").upper(),
        logging.INFO,
    )
    if not isinstance(level, int):
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="[%(name)s] %(message)s",
        stream=sys.stderr,
    )
    uninstall()
