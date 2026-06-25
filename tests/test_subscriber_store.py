from pathlib import Path
from unittest.mock import Mock

from daglas.subscriber_store import SubscriberStore


class TestSubscriberStore:
    def test_list_empty(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.txt"))
        assert store.list() == []

    def test_add_and_list(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.txt"))
        store.add("alice@example.com")
        assert store.list() == ["alice@example.com"]

    def test_remove(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.txt"))
        store.add("alice@example.com")
        store.remove("alice@example.com")
        assert store.list() == []

    def test_add_duplicate(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.txt"))
        store.add("alice@example.com")
        store.add("alice@example.com")
        assert store.list() == ["alice@example.com"]

    def test_remove_nonexistent(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.txt"))
        store.add("alice@example.com")
        store.remove("bob@example.com")
        assert store.list() == ["alice@example.com"]

    def test_remove_missing_file(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.txt"))
        store.remove("alice@example.com")
        assert store.list() == []

    def test_strips_whitespace(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.txt"))
        store.add("  alice@example.com  ")
        assert store.list() == ["alice@example.com"]
        store.remove("  alice@example.com  ")
        assert store.list() == []


class TestUserNotes:
    def test_note_created_on_subscribe(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.txt"))
        store.handle_email("alice@gmail.com", "subscribe", "add me")
        note_path = tmp_path / "notes" / "alice_gmail_com.txt"
        assert note_path.is_file()
        content = note_path.read_text()
        assert "Email: alice@gmail.com" in content
        assert "Date:" in content

    def test_note_appended(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.txt"))
        store.handle_email("alice@gmail.com", "subscribe", "first message")
        store.handle_email("alice@gmail.com", "unsubscribe", "second message")
        note_path = tmp_path / "notes" / "alice_gmail_com.txt"
        content = note_path.read_text()
        assert "first message" in content
        assert "second message" in content

    def test_name_persisted_in_header(self, tmp_path: Path):
        store = SubscriberStore(
            path=str(tmp_path / "subs.txt"),
            llm=_mock_llm("Alice"),
        )
        store.handle_email("alice@gmail.com", "subscribe", "add me")
        note_path = tmp_path / "notes" / "alice_gmail_com.txt"
        content = note_path.read_text()
        assert "Name: Alice" in content

    def test_read_user_name(self, tmp_path: Path):
        store = SubscriberStore(
            path=str(tmp_path / "subs.txt"),
            llm=_mock_llm("Alice"),
        )
        store.handle_email("alice@gmail.com", "subscribe", "add me")
        assert store._notes.read_user_name("alice@gmail.com") == "Alice"

    def test_read_user_name_missing_file(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.txt"))
        assert store._notes.read_user_name("nobody@example.com") is None

    def test_unsubscribe_reads_stored_name(self, tmp_path: Path):
        sender_mock = Mock()
        llm_mock = _mock_llm("Alice")
        store = SubscriberStore(
            path=str(tmp_path / "subs.txt"),
            sender_queue=sender_mock,
            llm=llm_mock,
        )
        store.handle_email("alice@gmail.com", "subscribe", "add me")
        llm_mock.reset_mock()
        store.handle_email("alice@gmail.com", "unsubscribe", "remove me")
        llm_mock.assert_not_called()
        sent_req = sender_mock.push.call_args[0][0]
        assert "Alice" in sent_req.text_body


class TestNameExtraction:
    def test_extract_uses_llm(self):
        llm = _mock_llm("Bob")
        store = SubscriberStore(path="/tmp/_test_sub_only", llm=llm)
        name = store._extract_user_name("hey", "body", "bob@example.com")
        assert name == "Bob"

    def test_fallback_when_llm_returns_none(self):
        llm = _mock_llm("NONE")
        store = SubscriberStore(path="/tmp/_test_sub_only", llm=llm)
        name = store._extract_user_name("hey", "body", "bob@example.com")
        assert name == "Bob"

    def test_no_llm_uses_local_part(self):
        store = SubscriberStore(path="/tmp/_test_sub_only")
        name = store._extract_user_name("hey", "body", "bob@example.com")
        assert name == "Bob"


class TestHandleEmailExtended:
    def test_no_sender_no_confirmation(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.txt"))
        store.handle_email("alice@example.com", "subscribe", "add me")
        assert store.list() == ["alice@example.com"]

    def test_no_match_no_note_no_confirmation(self, tmp_path: Path):
        sender_mock = Mock()
        store = SubscriberStore(
            path=str(tmp_path / "subs.txt"),
            sender_queue=sender_mock,
        )
        store.handle_email("alice@example.com", "hello", "just checking")
        assert store.list() == []
        sender_mock.push.assert_not_called()
        notes = (
            list((tmp_path / "notes").iterdir())
            if (tmp_path / "notes").is_dir()
            else []
        )
        assert len(notes) == 0


def _mock_llm(return_value: str):
    m = Mock()
    m.prompt.return_value = return_value
    return m
