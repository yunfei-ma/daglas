#!/usr/bin/env python3
"""Verify Heartbeat tick() works against real config.

Usage:
    python scripts/heartbeat_verify.py

Prints the scheduled actions that would be due right now
and which pollers are registered, without executing anything.
"""

import sys
from datetime import datetime

from daglas import config as daglas_config
from daglas.config import load_config
from daglas.heartbeat import Heartbeat


def main() -> None:
    daglas_config.config = load_config()
    hb = Heartbeat()

    now = datetime.now()
    due = hb.tick(now=now)

    print(f"Time: {now.isoformat()}")
    print(f"Schedule date: {hb._schedule_date}")
    print(f"Schedule entries: {len(hb._schedule)}")
    for entry in hb._schedule:
        status = "COMPLETED" if entry.is_completed else "pending"
        print(f"  {entry.at} {entry.action} [{status}]")
    print(f"Due now: {due}")
    print(f"Pollers: {len(hb._pollers)}")
    for poller in hb._pollers:
        print(f"  {poller.name} every {poller.interval_seconds}s")

    print("\nHeartbeat tick is sovereign — no actions were executed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
