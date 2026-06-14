from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from daglas.email_sender_queue import EmailSenderQueue, SendRequest


def _make_queue(tmp_path: Path) -> EmailSenderQueue:
    queue = EmailSenderQueue()
    queue._immediate_path = tmp_path / "immediate.jsonl"
    queue._scheduled_path = tmp_path / "scheduled.jsonl"
    queue._sender = _MockSender()
    return queue


class _MockSender:
    def send(self, email, recipients):
        from daglas.email_sender import SendResult

        return SendResult(success_count=len(recipients))


class TestPush:
    def test_push_immediate_writes_file(self, tmp_path: Path):
        queue = _make_queue(tmp_path)
        queue.push(SendRequest(to=["a@b.com"], subject="hi", send_at="immediate"))
        assert queue._immediate_path.is_file()
        lines = queue._immediate_path.read_text().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["subject"] == "hi"
        assert data["send_at"] == "immediate"

    def test_push_scheduled_writes_file(self, tmp_path: Path):
        queue = _make_queue(tmp_path)
        queue.push(
            SendRequest(to=["a@b.com"], subject="hi", send_at="2026-06-15T07:00:00")
        )
        assert queue._scheduled_path.is_file()
        lines = queue._scheduled_path.read_text().splitlines()
        assert len(lines) == 1

    def test_push_unspecified_skips(self, tmp_path: Path):
        queue = _make_queue(tmp_path)
        queue.push(SendRequest(to=["a@b.com"], subject="hi", send_at=None))
        assert not queue._immediate_path.is_file()
        assert not queue._scheduled_path.is_file()

    def test_push_invalid_skips(self, tmp_path: Path):
        queue = _make_queue(tmp_path)
        queue.push(SendRequest(to=["a@b.com"], subject="hi", send_at="garbage"))
        assert not queue._immediate_path.is_file()
        assert not queue._scheduled_path.is_file()

    def test_push_adds_queued_at(self, tmp_path: Path):
        queue = _make_queue(tmp_path)
        queue.push(SendRequest(to=["a@b.com"], subject="hi", send_at="immediate"))
        data = json.loads(queue._immediate_path.read_text().splitlines()[0])
        assert data["queued_at"] != ""


class TestReadAll:
    def test_read_all_empty(self, tmp_path: Path):
        queue = _make_queue(tmp_path)
        assert queue._read_all(queue._immediate_path) == []

    def test_read_all_removes_file(self, tmp_path: Path):
        queue = _make_queue(tmp_path)
        queue._immediate_path.write_text('{"subject":"a"}\n{"subject":"b"}\n')
        lines = queue._read_all(queue._immediate_path)
        assert len(lines) == 2
        assert not queue._immediate_path.is_file()


class TestDispatch:
    def test_dispatch_calls_sender(self, tmp_path: Path):
        calls = []
        queue = _make_queue(tmp_path)
        queue._sender = _MockSender()
        original_send = queue._sender.send

        def track_send(email, recipients):
            calls.append((email.subject, recipients))
            return original_send(email, recipients)

        queue._sender.send = track_send
        queue.dispatch(
            SendRequest(
                to=["a@b.com"],
                subject="test",
                body="hello",
                send_at="immediate",
            )
        )
        assert len(calls) == 1
        assert calls[0] == ("test", ["a@b.com"])


class TestScheduledLoop:
    def test_dispatches_past_due(self, tmp_path: Path):
        queue = _make_queue(tmp_path)
        requests = []
        queue._dispatch = lambda r: requests.append(r)

        queue._scheduled_path.write_text(
            json.dumps(
                {
                    "to": ["a@b.com"],
                    "subject": "past",
                    "body": "",
                    "html_body": "",
                    "send_at": "2020-01-01T00:00:00",
                    "queued_at": "",
                }
            )
            + "\n"
        )
        lines = queue._read_all(queue._scheduled_path)
        for line in lines:
            req = queue._deserialize(line)
            queue._dispatch(req)
        assert len(requests) == 1
        assert requests[0].subject == "past"

    def test_keeps_future(self, tmp_path: Path):
        queue = _make_queue(tmp_path)
        dispatched = []
        queue.dispatch = lambda r: dispatched.append(r)

        future = "2099-12-31T23:59:00+00:00"
        data = {
            "to": ["a@b.com"],
            "subject": "future",
            "body": "",
            "html_body": "",
            "send_at": future,
            "queued_at": "",
        }
        queue._scheduled_path.write_text(json.dumps(data) + "\n")

        lines = queue._read_all(queue._scheduled_path)
        for line in lines:
            req = queue._deserialize(line)
            send_at = datetime.fromisoformat(req.send_at)
            now = datetime.now(timezone.utc)
            if send_at <= now:
                queue.dispatch(req)
            else:
                queue._write_all(queue._scheduled_path, [line])

        assert len(dispatched) == 0
        assert queue._scheduled_path.is_file()
        remaining = json.loads(queue._scheduled_path.read_text().splitlines()[0])
        assert remaining["subject"] == "future"


class TestLifecycle:
    def test_start_stop(self):
        queue = EmailSenderQueue()
        queue._immediate_thread = threading.Thread(target=lambda: None)
        queue._scheduled_thread = threading.Thread(target=lambda: None)
        queue._stop_event = threading.Event()
        assert not queue._stop_event.is_set()
        queue.stop()

    def test_immediate_loop_exits_on_stop(self, tmp_path: Path):
        queue = _make_queue(tmp_path)
        queue.start()
        assert queue._immediate_thread is not None
        assert queue._immediate_thread.is_alive()
        assert queue._scheduled_thread is not None
        assert queue._scheduled_thread.is_alive()
        queue.stop()
        assert not queue._immediate_thread.is_alive()
        assert not queue._scheduled_thread.is_alive()
