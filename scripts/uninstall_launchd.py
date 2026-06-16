from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"

OUTBOUND_LABEL = "com.daglas.outbound"
RUNNER_LABEL = "com.daglas.runner"

OUTBOUND_PLIST = LAUNCH_AGENTS_DIR / f"{OUTBOUND_LABEL}.plist"
RUNNER_PLIST = LAUNCH_AGENTS_DIR / f"{RUNNER_LABEL}.plist"

logger = logging.getLogger("uninstall_launchd")


def _check_macos() -> None:
    if sys.platform != "darwin":
        print("ERROR: launchd is only available on macOS.")
        sys.exit(1)


def _unload(path: Path) -> None:
    if not path.is_file():
        return
    result = subprocess.run(
        ["launchctl", "unload", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"WARN: launchctl unload failed for {path.name}: {result.stderr.strip()}")
    else:
        logger.info("Unloaded %s", path.name)


def _remove(path: Path) -> None:
    if path.is_file():
        path.unlink()
        logger.info("Removed %s", path)


def uninstall() -> None:
    _check_macos()

    for path in (OUTBOUND_PLIST, RUNNER_PLIST):
        _unload(path)
        _remove(path)

    print("Uninstalled: com.daglas.outbound, com.daglas.runner")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[%(name)s] %(message)s",
        stream=sys.stderr,
    )
    uninstall()
