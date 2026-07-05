from __future__ import annotations

from datetime import date, time

from daglas.heartbeat import Heartbeat


def _set_schedule(heartbeat, today: date, entries: list[tuple[str, str]]) -> None:
    """Set up a schedule directly without config dependency."""
    from daglas.heartbeat import ScheduleEntry

    heartbeat._schedule_date = today
    heartbeat._schedule = sorted(
        (ScheduleEntry(at=at, action=action) for at, action in entries),
        key=lambda e: e.at,
    )


class TestGetSchedule:
    def test_same_date_returns_due(self):
        hb = Heartbeat()
        today = date(2026, 7, 5)
        _set_schedule(hb, today, [("06:00", "fetch"), ("07:00", "send")])
        assert hb.get_schedule(today, time(7, 0)) == ["fetch", "send"]

    def test_date_rollover_triggers_rebuild(self):
        hb = Heartbeat()
        old = date(2026, 7, 4)
        _set_schedule(hb, old, [("06:00", "fetch")])
        new = date(2026, 7, 5)
        result = hb.get_schedule(new, time(8, 0))
        assert hb._schedule_date == new
        assert "fetch" in result

    def test_old_date_returns_empty(self):
        hb = Heartbeat()
        _set_schedule(hb, date(2026, 7, 5), [("06:00", "fetch")])
        assert hb.get_schedule(date(2026, 7, 4), time(8, 0)) == []

    def test_before_time_returns_empty(self):
        hb = Heartbeat()
        today = date(2026, 7, 5)
        _set_schedule(hb, today, [("06:00", "fetch")])
        assert hb.get_schedule(today, time(5, 0)) == []

    def test_at_time_returns_action(self):
        hb = Heartbeat()
        today = date(2026, 7, 5)
        _set_schedule(hb, today, [("06:00", "fetch")])
        assert hb.get_schedule(today, time(6, 0)) == ["fetch"]

    def test_after_time_returns_action(self):
        hb = Heartbeat()
        today = date(2026, 7, 5)
        _set_schedule(hb, today, [("06:00", "fetch")])
        assert hb.get_schedule(today, time(7, 0)) == ["fetch"]

    def test_completed_not_returned(self):
        hb = Heartbeat()
        today = date(2026, 7, 5)
        _set_schedule(hb, today, [("06:00", "fetch")])
        hb.set_complete("fetch")
        assert hb.get_schedule(today, time(7, 0)) == []

    def test_multiple_due(self):
        hb = Heartbeat()
        today = date(2026, 7, 5)
        _set_schedule(hb, today, [("06:00", "fetch"), ("06:30", "send")])
        assert hb.get_schedule(today, time(7, 0)) == ["fetch", "send"]

    def test_only_one_due_when_other_not_yet(self):
        hb = Heartbeat()
        today = date(2026, 7, 5)
        _set_schedule(hb, today, [("06:00", "fetch"), ("07:00", "send")])
        assert hb.get_schedule(today, time(6, 30)) == ["fetch"]


class TestTick:
    def test_tick_does_not_execute_actions(self):
        hb = Heartbeat()
        today = date(2026, 7, 5)
        _set_schedule(hb, today, [("06:00", "fetch")])

        class FakeNow:
            def date(self):
                return today

            def time(self):
                return time(7, 0)

        names = hb.tick(now=FakeNow())
        assert names == ["fetch"]

    def test_tick_after_set_complete(self):
        hb = Heartbeat()
        today = date(2026, 7, 5)
        _set_schedule(hb, today, [("06:00", "fetch")])
        hb.set_complete("fetch")

        class FakeNow:
            def date(self):
                return today

            def time(self):
                return time(7, 0)

        assert hb.tick(now=FakeNow()) == []


class TestSetComplete:
    def test_set_complete_existing(self):
        hb = Heartbeat()
        today = date(2026, 7, 5)
        _set_schedule(hb, today, [("06:00", "fetch")])
        hb.set_complete("fetch")
        assert hb._schedule[0].is_completed is True

    def test_set_complete_nonexistent(self):
        hb = Heartbeat()
        today = date(2026, 7, 5)
        _set_schedule(hb, today, [("06:00", "fetch")])
        hb.set_complete("nonexistent")

    def test_set_complete_idempotent(self):
        hb = Heartbeat()
        today = date(2026, 7, 5)
        _set_schedule(hb, today, [("06:00", "fetch")])
        hb.set_complete("fetch")
        hb.set_complete("fetch")
        assert hb._schedule[0].is_completed is True


class TestPollers:
    def test_poller_fires_at_interval(self):
        hb = Heartbeat()
        calls = []
        hb.add_poller("test", 30, lambda: calls.append("fired"))
        hb.run_due_pollers(now_monotonic=0)
        assert len(calls) == 1
        hb.run_due_pollers(now_monotonic=30)
        assert len(calls) == 2

    def test_poller_does_not_fire_early(self):
        hb = Heartbeat()
        calls = []
        hb.add_poller("test", 30, lambda: calls.append("fired"))
        hb.run_due_pollers(now_monotonic=0)
        assert len(calls) == 1
        hb.run_due_pollers(now_monotonic=15)
        assert len(calls) == 1

    def test_poller_resets_after_execution(self):
        hb = Heartbeat()
        calls = []
        hb.add_poller("test", 30, lambda: calls.append("fired"))
        hb.run_due_pollers(now_monotonic=0)
        hb.run_due_pollers(now_monotonic=30)
        assert len(calls) == 2
        hb.run_due_pollers(now_monotonic=45)
        assert len(calls) == 2
        hb.run_due_pollers(now_monotonic=60)
        assert len(calls) == 3


class TestStop:
    def test_stop_clears_shutdown_event(self):
        hb = Heartbeat()
        assert not hb._shutdown.is_set()
        hb.stop()
        assert hb._shutdown.is_set()
