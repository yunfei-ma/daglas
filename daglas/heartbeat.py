from __future__ import annotations

import logging
import threading
import time as _time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time

import daglas.config

logger = logging.getLogger(__name__)


@dataclass
class ScheduleEntry:
    at: str
    action: str
    is_completed: bool = False


@dataclass
class Poller:
    name: str
    interval_seconds: int
    callback: Callable[[], None]
    last_run: float = 0.0


class Heartbeat:
    def __init__(self) -> None:
        self._schedule_date: date | None = None
        self._schedule: list[ScheduleEntry] = []
        self._pollers: list[Poller] = []
        self._shutdown = threading.Event()

    # --- Schedule (once-per-day actions) ---

    def _rebuild_schedule(self, today: date) -> None:
        cfg = daglas.config.config
        if cfg and getattr(cfg, "heartbeat_schedule", None):
            raw = cfg.heartbeat_schedule
        elif cfg:
            raw = [
                {"at": cfg.fetch_time, "action": "fetch"},
                {"at": cfg.send_time, "action": "send"},
            ]
        else:
            raw = [
                {"at": "06:00", "action": "fetch"},
                {"at": "07:00", "action": "send"},
            ]
        self._schedule = sorted(
            (ScheduleEntry(at=entry["at"], action=entry["action"]) for entry in raw),
            key=lambda e: e.at,
        )
        self._schedule_date = today

    @staticmethod
    def _parse_time(at: str) -> time:
        hour, minute = at.split(":")
        return time(int(hour), int(minute))

    def get_schedule(self, today: date, now: time) -> list[str]:
        if self._schedule_date is None or today > self._schedule_date:
            self._rebuild_schedule(today)
        elif today < self._schedule_date:
            return []
        return [
            entry.action
            for entry in self._schedule
            if not entry.is_completed and self._parse_time(entry.at) <= now
        ]

    def set_complete(self, name: str) -> None:
        for entry in self._schedule:
            if entry.action == name:
                entry.is_completed = True
                return

    # --- Pollers (interval-based actions) ---

    def add_poller(
        self, name: str, interval_seconds: int, callback: Callable[[], None]
    ) -> None:
        self._pollers.append(
            Poller(name, interval_seconds, callback, last_run=-interval_seconds)
        )

    def run_due_pollers(self, now_monotonic: float | None = None) -> None:
        if now_monotonic is None:
            now_monotonic = _time.monotonic()
        for poller in self._pollers:
            if now_monotonic - poller.last_run >= poller.interval_seconds:
                try:
                    poller.callback()
                except Exception:
                    logger.exception("Poller %s failed", poller.name)
                poller.last_run = now_monotonic

    # --- Tick ---

    def tick(self, now: datetime | None = None) -> list[str]:
        if now is None:
            now = datetime.now()
        return self.get_schedule(now.date(), now.time())

    # --- Lifecycle ---

    def stop(self) -> None:
        self._shutdown.set()
