from unittest.mock import MagicMock, patch

import daglas.config as daglas_config
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


class TestCheckOnce:
    def test_check_once_pushes_to_queue(self, tmp_path):
        queue = MagicMock()
        receiver = EmailReceiver(queue)

        def mock_connect():
            conn = MockIMAP()
            conn.messages = {
                b"1": _make_raw_email("alice@example.com", "hello", "world"),
            }
            return conn

        with patch.object(receiver, "_connect", mock_connect):
            count = receiver.check_once()
        assert count == 1
        queue.push.assert_called_once()
        args = queue.push.call_args
        assert args[0][0] == "incoming"
        assert args[0][1].sender == "alice@example.com"
        assert args[0][1].subject == "hello"
        assert args[0][1].body == "world"

    def test_check_once_marks_seen(self, tmp_path):
        queue = MagicMock()
        receiver = EmailReceiver(queue)

        conn = MockIMAP()
        conn.messages = {
            b"1": _make_raw_email("alice@example.com", "hello", "world"),
        }

        with patch.object(receiver, "_connect", lambda: conn):
            receiver.check_once()

        assert len(conn.stored_flags) == 1
        assert conn.stored_flags[0][0] == b"1"

    def test_check_once_no_unseen(self, tmp_path):
        queue = MagicMock()
        receiver = EmailReceiver(queue)

        def mock_connect():
            return MockIMAP()

        with patch.object(receiver, "_connect", mock_connect):
            count = receiver.check_once()
        assert count == 0
        queue.push.assert_not_called()

    def test_check_once_imap_unreachable(self, tmp_path):
        queue = MagicMock()
        receiver = EmailReceiver(queue)

        def broken_connect():
            raise ConnectionError("Connection refused")

        with patch.object(receiver, "_connect", broken_connect):
            count = receiver.check_once()
        assert count == 0
        queue.push.assert_not_called()

    def test_check_once_bad_message_skips(self, tmp_path):
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
            count = receiver.check_once()
        assert count == 1
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
            receiver.check_once()

        pushed = queue.push.call_args[0][1]
        assert pushed.sender == "alice@example.com"
        assert pushed.subject == "hello"
        assert pushed.body == "body text"
        assert pushed.raw_bytes == raw_bytes


class TestRunLoop:
    def test_run_loop_interval_timing(self, tmp_path):
        queue = MagicMock()
        mock_cfg = MagicMock(email_receiver_poll_interval=0.02)
        with patch.object(daglas_config, "config", mock_cfg):
            receiver = EmailReceiver(queue)

        with (
            patch.object(receiver, "_connect", lambda: MockIMAP()),
            patch("time.sleep") as mock_sleep,
        ):
            receiver.run_loop(max_iterations=2)

        assert mock_sleep.call_count == 1
        sleep_arg = mock_sleep.call_args[0][0]
        assert 0 <= sleep_arg <= 0.02

    def test_run_loop_no_sleep_when_poll_exceeds_interval(self, tmp_path):
        queue = MagicMock()
        mock_cfg = MagicMock(email_receiver_poll_interval=0.01)
        with patch.object(daglas_config, "config", mock_cfg):
            receiver = EmailReceiver(queue)

        elapsed_seconds = 100.0
        monotonic_values = [0.0, elapsed_seconds, elapsed_seconds, elapsed_seconds]
        call_count = 0

        def fake_monotonic():
            nonlocal call_count
            val = monotonic_values[call_count]
            call_count += 1
            return val

        with (
            patch.object(receiver, "_connect", lambda: MockIMAP()),
            patch("time.monotonic", fake_monotonic),
            patch("time.sleep") as mock_sleep,
        ):
            receiver.run_loop(max_iterations=2)

        assert mock_sleep.call_count == 1
        assert mock_sleep.call_args[0][0] == 0.0


class TestLifecycle:
    def test_is_running_after_start(self):
        queue = MagicMock()
        receiver = EmailReceiver(queue)

        with patch.object(receiver, "_connect", lambda: MockIMAP()):
            receiver.start()
        assert receiver.is_running is True
        receiver.stop()

    def test_stop_clears_is_running(self):
        queue = MagicMock()
        receiver = EmailReceiver(queue)

        with patch.object(receiver, "_connect", lambda: MockIMAP()):
            receiver.start()
        assert receiver.is_running is True
        receiver.stop()
        assert receiver.is_running is False

    def test_start_idempotent(self):
        queue = MagicMock()
        receiver = EmailReceiver(queue)

        with (
            patch.object(receiver, "_connect", lambda: MockIMAP()),
            patch.object(receiver, "check_once", return_value=0),
        ):
            receiver.start()
            thread_id = id(receiver._thread)
            receiver.start()
            assert id(receiver._thread) == thread_id
            receiver.stop()

    def test_stop_without_start_is_safe(self):
        queue = MagicMock()
        receiver = EmailReceiver(queue)
        receiver.stop()
        assert receiver.is_running is False

    def test_run_loop_obeys_stop_event(self):
        queue = MagicMock()
        mock_cfg = MagicMock(email_receiver_poll_interval=0.01)
        with patch.object(daglas_config, "config", mock_cfg):
            receiver = EmailReceiver(queue)

        with patch.object(receiver, "_connect", lambda: MockIMAP()):
            receiver.start()
            receiver.stop()

        assert receiver.is_running is False
