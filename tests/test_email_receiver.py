from unittest.mock import MagicMock, patch

from daglas.email_receiver import EmailReceiver


def _make_raw_email(sender: str, subject: str, body: str) -> bytes:
    return f"From: {sender}\r\nSubject: {subject}\r\n\r\n{body}".encode("utf-8")


class MockIMAP:
    def __init__(self):
        self.messages: dict[bytes, bytes] = {}
        self.stored_flags: list[tuple[bytes, str, str]] = []

    def login(self, user, password):
        pass

    def select(self, mailbox):
        pass

    def search(self, charset, criterion):
        unseen = list(self.messages.keys())
        return "OK", [b" ".join(unseen)]

    def fetch(self, msg_id, parts):
        raw = self.messages[msg_id]
        return "OK", [(b"", raw)]

    def store(self, msg_id, flag, value):
        self.stored_flags.append((msg_id, flag, value))

    def logout(self):
        pass


class TestPoll:
    def test_poll_pushes_to_queue(self, tmp_path):
        queue = MagicMock()
        receiver = EmailReceiver(queue)

        def mock_connect():
            conn = MockIMAP()
            conn.messages = {
                b"1": _make_raw_email("alice@example.com", "hello", "world"),
            }
            return conn

        with patch.object(receiver, "_connect", mock_connect):
            receiver.poll()
        queue.push.assert_called_once()
        args = queue.push.call_args
        assert args[0][0] == "incoming"
        assert args[0][1].sender == "alice@example.com"
        assert args[0][1].subject == "hello"
        assert args[0][1].body == "world"

    def test_poll_marks_seen(self, tmp_path):
        queue = MagicMock()
        receiver = EmailReceiver(queue)

        conn = MockIMAP()
        conn.messages = {
            b"1": _make_raw_email("alice@example.com", "hello", "world"),
        }

        with patch.object(receiver, "_connect", lambda: conn):
            receiver.poll()

        assert len(conn.stored_flags) == 1
        assert conn.stored_flags[0][0] == b"1"

    def test_poll_no_unseen(self, tmp_path):
        queue = MagicMock()
        receiver = EmailReceiver(queue)

        def mock_connect():
            return MockIMAP()

        with patch.object(receiver, "_connect", mock_connect):
            receiver.poll()
        queue.push.assert_not_called()

    def test_poll_imap_unreachable(self, tmp_path):
        queue = MagicMock()
        receiver = EmailReceiver(queue)

        def broken_connect():
            raise ConnectionError("Connection refused")

        with patch.object(receiver, "_connect", broken_connect):
            receiver.poll()
        queue.push.assert_not_called()

    def test_poll_bad_message_skips(self, tmp_path):
        queue = MagicMock()
        receiver = EmailReceiver(queue)

        def mock_connect():
            conn = MockIMAP()
            conn.messages = {
                b"1": b"garbage data",
                b"2": _make_raw_email("bob@example.com", "subscribe", "add me"),
            }
            return conn

        with patch.object(receiver, "_connect", mock_connect):
            receiver.poll()
        assert queue.push.call_count == 1

    def test_raw_email_has_all_fields(self, tmp_path):
        queue = MagicMock()
        receiver = EmailReceiver(queue)
        raw_bytes = _make_raw_email("alice@example.com", "hello", "body text")

        def mock_connect():
            conn = MockIMAP()
            conn.messages = {b"1": raw_bytes}
            return conn

        with patch.object(receiver, "_connect", mock_connect):
            receiver.poll()

        pushed = queue.push.call_args[0][1]
        assert pushed.sender == "alice@example.com"
        assert pushed.subject == "hello"
        assert pushed.body == "body text"
        assert pushed.raw_bytes == raw_bytes
