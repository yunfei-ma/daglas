from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from daglas.email_sender_queue import EmailSenderQueue, MailItem


def _make_queue(tmp_path: Path) -> EmailSenderQueue:
    q = EmailSenderQueue()
    q._immediate_path = tmp_path / "immediate.jsonl"
    q._scheduled_path = tmp_path / "scheduled.jsonl"
    q._sender = _MockSender()
    return q


class _MockSender:
    def send(self, email, recipients):
        from daglas.email_sender import SendResult

        return SendResult(success_count=len(recipients))


class TestPush:
    def test_push_immediate_writes_file(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        q.push(
            MailItem(to=["a@b.com"], subject="hi", text_body="", send_at="immediate")
        )
        assert q._immediate_path.is_file()
        data = json.loads(q._immediate_path.read_text().splitlines()[0])
        assert data["subject"] == "hi"
        assert data["send_at"] == "immediate"
        assert data["request_id"] != ""

    def test_push_scheduled_writes_file(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        q.push(
            MailItem(
                to=["a@b.com"],
                subject="hi",
                text_body="hello",
                send_at="2026-06-15T07:00:00",
            )
        )
        assert q._scheduled_path.is_file()
        data = json.loads(q._scheduled_path.read_text().splitlines()[0])
        assert data["send_at"] == "2026-06-15T07:00:00"

    def test_push_unspecified_skips(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        q.push(MailItem(to=["a@b.com"], subject="hi", text_body="hello", send_at=None))
        assert not q._immediate_path.is_file()
        assert not q._scheduled_path.is_file()

    def test_push_invalid_skips(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        q.push(
            MailItem(to=["a@b.com"], subject="hi", text_body="hello", send_at="garbage")
        )
        assert not q._immediate_path.is_file()
        assert not q._scheduled_path.is_file()

    def test_push_adds_queued_at(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        q.push(
            MailItem(to=["a@b.com"], subject="hi", text_body="", send_at="immediate")
        )
        data = json.loads(q._immediate_path.read_text().splitlines()[0])
        assert data["queued_at"] != ""

    def test_push_immediate_triggers_notify(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        q._immediate_notify.clear()
        q.push(
            MailItem(to=["a@b.com"], subject="hi", text_body="", send_at="immediate")
        )
        assert q._immediate_notify.is_set()

    def test_push_scheduled_maintains_sort_order(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        q.push(
            MailItem(
                to=["a@b.com"],
                subject="second",
                text_body="b",
                send_at="2026-06-15T08:00:00",
            )
        )
        q.push(
            MailItem(
                to=["a@b.com"],
                subject="first",
                text_body="a",
                send_at="2026-06-15T07:00:00",
            )
        )
        lines = q._scheduled_path.read_text().splitlines()
        subjects = [json.loads(line)["subject"] for line in lines]
        assert subjects == ["first", "second"]


class TestPop:
    def test_pop_returns_item(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        q._immediate_path.write_text(
            json.dumps(
                {
                    "request_id": "11111111-1111-4111-8111-111111111111",
                    "to": ["a@b.com"],
                    "subject": "test",
                    "text_body": "hello",
                    "html_body": "",
                    "send_at": "immediate",
                    "queued_at": "2026-06-15T07:00:00+00:00",
                }
            )
            + "\n"
        )
        item = q._pop()
        assert item is not None
        assert item.subject == "test"
        assert item.text_body == "hello"

    def test_pop_removes_item(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        q._immediate_path.write_text(
            json.dumps(
                {
                    "request_id": "11111111-1111-4111-8111-111111111111",
                    "to": ["a@b.com"],
                    "subject": "test",
                    "text_body": "hello",
                    "html_body": "",
                    "send_at": "immediate",
                    "queued_at": "",
                }
            )
            + "\n"
        )
        q._pop()
        assert not q._immediate_path.is_file()

    def test_pop_empty_returns_none(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        assert q._pop() is None


class TestGetNextDue:
    def test_empty_returns_none(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        assert q.get_next_due(datetime.now(timezone.utc)) is None

    def test_returns_within_window(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        q._scheduled_path.write_text(
            json.dumps(
                {
                    "request_id": "11111111-1111-4111-8111-111111111111",
                    "to": ["a@b.com"],
                    "subject": "within",
                    "text_body": "hello",
                    "html_body": "",
                    "send_at": "2026-06-15T12:15:00+00:00",
                    "queued_at": "",
                }
            )
            + "\n"
        )
        item = q.get_next_due(now)
        assert item is not None
        assert item.subject == "within"

    def test_skips_beyond_window(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        q._scheduled_path.write_text(
            json.dumps(
                {
                    "request_id": "11111111-1111-4111-8111-111111111111",
                    "to": ["a@b.com"],
                    "subject": "far",
                    "text_body": "hello",
                    "html_body": "",
                    "send_at": "2026-06-15T13:00:00+00:00",
                    "queued_at": "",
                }
            )
            + "\n"
        )
        assert q.get_next_due(now) is None

    def test_returns_first_due_in_order(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        q._scheduled_path.write_text(
            json.dumps(
                {
                    "request_id": "11111111-1111-4111-8111-111111111111",
                    "to": ["a@b.com"],
                    "subject": "first",
                    "text_body": "a",
                    "html_body": "",
                    "send_at": "2026-06-15T12:10:00+00:00",
                    "queued_at": "",
                }
            )
            + "\n"
            + json.dumps(
                {
                    "request_id": "22222222-2222-4222-8222-222222222222",
                    "to": ["a@b.com"],
                    "subject": "second",
                    "text_body": "b",
                    "html_body": "",
                    "send_at": "2026-06-15T12:20:00+00:00",
                    "queued_at": "",
                }
            )
            + "\n"
        )
        item = q.get_next_due(now)
        assert item is not None
        assert item.subject == "first"

    def test_does_not_delete_entry(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        q._scheduled_path.write_text(
            json.dumps(
                {
                    "request_id": "11111111-1111-4111-8111-111111111111",
                    "to": ["a@b.com"],
                    "subject": "keep",
                    "text_body": "hello",
                    "html_body": "",
                    "send_at": "2026-06-15T12:10:00+00:00",
                    "queued_at": "",
                }
            )
            + "\n"
        )
        q.get_next_due(now)
        assert q._scheduled_path.is_file()


class TestDelete:
    def test_delete_removes_by_request_id(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        rid = UUID("11111111-1111-4111-8111-111111111111")
        other_rid = UUID("22222222-2222-4222-8222-222222222222")
        q._scheduled_path.write_text(
            json.dumps(
                {
                    "request_id": str(rid),
                    "to": ["a@b.com"],
                    "subject": "remove",
                    "text_body": "",
                    "html_body": "",
                    "send_at": "2026-06-15T12:00:00+00:00",
                    "queued_at": "",
                }
            )
            + "\n"
            + json.dumps(
                {
                    "request_id": str(other_rid),
                    "to": ["a@b.com"],
                    "subject": "keep",
                    "text_body": "",
                    "html_body": "",
                    "send_at": "2026-06-15T12:00:00+00:00",
                    "queued_at": "",
                }
            )
            + "\n"
        )
        q.delete(rid)
        remaining = json.loads(q._scheduled_path.read_text().splitlines()[0])
        assert remaining["subject"] == "keep"

    def test_delete_empty_file_safe(self, tmp_path: Path):
        q = _make_queue(tmp_path)
        q.delete(UUID("11111111-1111-4111-8111-111111111111"))


class TestLifecycle:
    def test_start_stop(self):
        q = EmailSenderQueue()
        q.start()
        assert q._immediate_thread is not None
        assert q._immediate_thread.is_alive()
        assert q._scheduled_thread is not None
        assert q._scheduled_thread.is_alive()
        q.stop()
        assert not q._immediate_thread.is_alive()
        assert not q._scheduled_thread.is_alive()
