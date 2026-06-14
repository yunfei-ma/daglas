from unittest.mock import MagicMock

from daglas.email_processor import EmailProcessor
from daglas.email_queue import RawEmail


def _make_email(sender="a@b.com", subject="hello", body="world", raw=b"raw"):
    return RawEmail(sender=sender, subject=subject, body=body, raw_bytes=raw)


class TestListener:
    def test_listener_called(self):
        queue = MagicMock()
        queue.drain.return_value = [
            _make_email(sender="a@b.com", subject="subscribe", body="add me"),
        ]
        processor = EmailProcessor(queue)
        calls = []
        processor.add_listener(lambda s, sub, b: calls.append((s, sub, b)))
        count = processor.process()
        assert count == 1
        assert calls == [("a@b.com", "subscribe", "add me")]

    def test_multiple_listeners(self):
        queue = MagicMock()
        queue.drain.return_value = [
            _make_email(sender="a@b.com", subject="hello", body="world"),
        ]
        processor = EmailProcessor(queue)
        calls_a = []
        calls_b = []
        processor.add_listener(lambda s, sub, b: calls_a.append((s, sub, b)))
        processor.add_listener(lambda s, sub, b: calls_b.append((s, sub, b)))
        processor.process()
        assert len(calls_a) == 1
        assert len(calls_b) == 1

    def test_multiple_emails(self):
        queue = MagicMock()
        queue.drain.return_value = [
            _make_email(sender="a@b.com"),
            _make_email(sender="b@b.com"),
            _make_email(sender="c@b.com"),
        ]
        processor = EmailProcessor(queue)
        calls = []
        processor.add_listener(lambda s, sub, b: calls.append(s))
        count = processor.process()
        assert count == 3
        assert calls == ["a@b.com", "b@b.com", "c@b.com"]

    def test_empty_drain(self):
        queue = MagicMock()
        queue.drain.return_value = []
        processor = EmailProcessor(queue)
        calls = []
        processor.add_listener(lambda s, sub, b: calls.append(s))
        count = processor.process()
        assert count == 0
        assert calls == []

    def test_listener_exception_isolated(self):
        queue = MagicMock()
        queue.drain.return_value = [
            _make_email(sender="a@b.com"),
        ]
        processor = EmailProcessor(queue)
        calls = []

        def broken(s, sub, b):
            raise ValueError("oops")

        def ok(s, sub, b):
            calls.append(s)

        processor.add_listener(broken)
        processor.add_listener(ok)
        count = processor.process()
        assert count == 1
        assert calls == ["a@b.com"]

    def test_running_guard(self):
        queue = MagicMock()
        queue.drain.return_value = [
            _make_email(sender="a@b.com"),
        ]
        processor = EmailProcessor(queue)

        calls = []

        def reentrant(s, sub, b):
            processor._on_notify("incoming")
            calls.append(s)

        processor.add_listener(reentrant)
        processor._on_notify("incoming")
        assert calls == ["a@b.com"]

    def test_process_returns_count(self):
        queue = MagicMock()
        queue.drain.return_value = [
            _make_email(sender="a@b.com"),
            _make_email(sender="b@b.com"),
        ]
        processor = EmailProcessor(queue)
        count = processor.process()
        assert count == 2


class TestRegistration:
    def test_listener_registered_on_init(self):
        queue = MagicMock()
        EmailProcessor(queue)
        queue.on_push.assert_called_once_with("incoming", queue.on_push.call_args[0][1])
        assert queue.on_push.call_args[0][0] == "incoming"
