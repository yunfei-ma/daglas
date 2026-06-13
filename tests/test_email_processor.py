from unittest.mock import MagicMock

from daglas.email_processor import EmailProcessor
from daglas.email_queue import RawEmail


def _make_email(sender="a@b.com", subject="hello", body="world", raw=b"raw"):
    return RawEmail(sender=sender, subject=subject, body=body, raw_bytes=raw)


class TestClassification:
    def test_classify_subscribe(self):
        processor = EmailProcessor(MagicMock())
        action = processor._classify("subscribe", "please add me")
        assert action == "subscribe"

    def test_classify_unsubscribe(self):
        processor = EmailProcessor(MagicMock())
        action = processor._classify("hello", "please unsubscribe me")
        assert action == "unsubscribe"

    def test_classify_case_insensitive(self):
        processor = EmailProcessor(MagicMock())
        action = processor._classify("Subscribe", "ADD ME")
        assert action == "subscribe"

    def test_classify_no_match(self):
        processor = EmailProcessor(MagicMock())
        action = processor._classify("hello", "just checking in")
        assert action == "unknown"

    def test_classify_unsubscribe_wins(self):
        processor = EmailProcessor(MagicMock())
        action = processor._classify("unsubscribe", "Actually I wanted to subscribe")
        assert action == "unsubscribe"

    def test_multiple_patterns_per_action(self):
        processor = EmailProcessor(MagicMock())
        processor.register("alert", lambda s, sub, b: None, ["urgent", "alert"])
        action = processor._classify("URGENT", "read this")
        assert action == "alert"
        action = processor._classify("this is an alert", "read this")
        assert action == "alert"


class TestDispatch:
    def test_actor_called(self):
        queue = MagicMock()
        queue.drain.return_value = [
            _make_email(sender="a@b.com", subject="subscribe", body="add me")
        ]
        processor = EmailProcessor(queue)
        result = processor.process()
        assert result.action_counts.get("subscribe") == 1

    def test_custom_actor_registration(self):
        queue = MagicMock()
        queue.drain.return_value = [
            _make_email(sender="a@b.com", subject="archive this", body="old"),
        ]
        processor = EmailProcessor(queue)
        calls = []
        processor.register(
            "archive", lambda s, sub, b: calls.append((s, sub, b)), ["archive"]
        )
        processor.process()
        assert len(calls) == 1
        assert calls[0][0] == "a@b.com"

    def test_actor_exception_isolated(self):
        queue = MagicMock()
        queue.drain.return_value = [
            _make_email(sender="a@b.com", subject="subscribe", body="add me"),
            _make_email(sender="b@b.com", subject="subscribe", body="add me too"),
        ]
        processor = EmailProcessor(queue)

        def broken(s, sub, b):
            if s == "a@b.com":
                raise ValueError("oops")

        processor.register("subscribe", broken)
        result = processor.process()
        assert len(result.errors) == 1
        assert "oops" in result.errors[0]

    def test_empty_drain(self):
        queue = MagicMock()
        queue.drain.return_value = []
        processor = EmailProcessor(queue)
        result = processor.process()
        assert result.action_counts == {}
        assert result.errors == []


class TestRegistration:
    def test_listener_registered_on_init(self):
        queue = MagicMock()
        EmailProcessor(queue)
        queue.on_push.assert_called_once_with("incoming", queue.on_push.call_args[0][1])
        assert queue.on_push.call_args[0][0] == "incoming"


class TestUnknownDefault:
    def test_unknown_email_pushed_to_archive(self, tmp_path):
        from daglas.email_queue import EmailQueue

        queue = EmailQueue(data_dir=str(tmp_path))
        processor = EmailProcessor(queue)
        processor._unknown_default("a@b.com", "hello", "nobody knows")
        archived = queue.pop("archive")
        assert archived is not None
        assert archived.sender == "a@b.com"
