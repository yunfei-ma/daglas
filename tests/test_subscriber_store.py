import json
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

from daglas.subscriber_store import Subscriber, SubscriberStore


class TestSubscriberDataclass:
    def test_to_dict(self):
        s = Subscriber(
            email="a@b.com",
            name="Alice",
            level="intermediate",
            joined_at="2026-06-25T10:00:00",
            vocab_count=10,
        )
        d = s.to_dict()
        assert d["email"] == "a@b.com"
        assert d["vocab_count"] == 10

    def test_from_dict(self):
        d = {"email": "b@c.com", "name": "Bob", "vocab_count": 5}
        s = Subscriber.from_dict(d)
        assert s.email == "b@c.com"
        assert s.vocab_count == 5
        assert s.level == ""

    def test_roundtrip_json(self):
        s1 = Subscriber(email="x@y.z", level="advanced", vocab_count=15)
        data = s1.to_dict()
        s2 = Subscriber.from_dict(data)
        assert s2.email == s1.email
        assert s2.level == s1.level
        assert s2.vocab_count == s1.vocab_count


class TestSubscriberStore:
    def test_list_empty(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.jsonl"))
        assert store.list() == []

    def test_add_and_list(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.jsonl"))
        sub = store.add("alice@example.com")
        subs = store.list()
        assert len(subs) == 1
        assert subs[0].email == "alice@example.com"
        assert "joined_at" in sub.to_dict()

    def test_add_with_name_and_level(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.jsonl"))
        sub = store.add(
            "alice@example.com", name="Alice", level="intermediate", vocab_count=10
        )
        assert sub.name == "Alice"
        assert sub.level == "intermediate"
        assert sub.vocab_count == 10
        loaded = store.list()[0]
        assert loaded.level == "intermediate"

    def test_get_existing(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.jsonl"))
        store.add("alice@example.com", name="Alice")
        sub = store.get("alice@example.com")
        assert sub is not None
        assert sub.name == "Alice"

    def test_get_nonexistent(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.jsonl"))
        assert store.get("nobody@example.com") is None

    def test_update_level(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.jsonl"))
        store.add("alice@example.com", level="beginner")
        updated = store.update("alice@example.com", level="intermediate")
        assert updated is not None
        assert updated.level == "intermediate"
        assert store.get("alice@example.com").level == "intermediate"

    def test_update_nonexistent(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.jsonl"))
        assert store.update("nobody@x.com", level="advanced") is None

    def test_remove(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.jsonl"))
        store.add("alice@example.com")
        store.remove("alice@example.com")
        assert store.list() == []

    def test_add_duplicate_upserts(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.jsonl"))
        store.add("alice@example.com", level="beginner")
        store.add("alice@example.com", level="intermediate")
        subs = store.list()
        assert len(subs) == 1
        assert subs[0].level == "intermediate"

    def test_remove_nonexistent(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.jsonl"))
        store.add("alice@example.com")
        store.remove("bob@example.com")
        assert len(store.list()) == 1

    def test_remove_missing_file(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.jsonl"))
        store.remove("alice@example.com")
        assert store.list() == []

    def test_jsonl_format(self, tmp_path: Path):
        path = tmp_path / "subs.jsonl"
        store = SubscriberStore(path=str(path))
        store.add("a@b.com", name="Alice")
        lines = path.read_text().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["email"] == "a@b.com"
        assert data["name"] == "Alice"

    def test_migration_from_txt(self, tmp_path: Path):
        txt_path = tmp_path / "subscribers.txt"
        txt_path.write_text("alice@example.com\nbob@example.com\n")
        mtime = datetime.now().isoformat()[:10]
        jsonl_path = tmp_path / "subscribers.jsonl"
        store = SubscriberStore(path=str(jsonl_path))
        assert jsonl_path.is_file()
        assert not txt_path.exists()
        subs = store.list()
        assert len(subs) == 2
        assert subs[0].email == "alice@example.com"
        assert subs[1].email == "bob@example.com"
        assert subs[0].joined_at.startswith(mtime) or subs[0].joined_at != ""

    def test_migration_skipped_when_jsonl_exists(self, tmp_path: Path):
        jsonl_path = tmp_path / "subscribers.jsonl"
        jsonl_path.write_text(
            json.dumps({"email": "alice@example.com", "name": "Alice"}) + "\n"
        )
        txt_path = tmp_path / "subscribers.txt"
        txt_path.write_text("bob@example.com\n")
        store = SubscriberStore(path=str(jsonl_path))
        assert txt_path.exists()
        subs = store.list()
        assert len(subs) == 1
        assert subs[0].email == "alice@example.com"


class TestUserNotes:
    def test_note_created_on_subscribe(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.jsonl"))
        store.handle_email("alice@gmail.com", "subscribe", "add me")
        note_path = tmp_path / "notes" / "alice_gmail_com.txt"
        assert note_path.is_file()
        content = note_path.read_text()
        assert "Email: alice@gmail.com" in content
        assert "Date:" in content

    def test_note_appended(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.jsonl"))
        store.handle_email("alice@gmail.com", "subscribe", "first message")
        store.handle_email("alice@gmail.com", "unsubscribe", "second message")
        note_path = tmp_path / "notes" / "alice_gmail_com.txt"
        content = note_path.read_text()
        assert "first message" in content
        assert "second message" in content

    def test_name_persisted_in_header(self, tmp_path: Path):
        store = SubscriberStore(
            path=str(tmp_path / "subs.jsonl"),
            llm=_mock_llm("Alice"),
        )
        store.handle_email("alice@gmail.com", "subscribe", "add me")
        note_path = tmp_path / "notes" / "alice_gmail_com.txt"
        content = note_path.read_text()
        assert "Name: Alice" in content

    def test_read_user_name(self, tmp_path: Path):
        store = SubscriberStore(
            path=str(tmp_path / "subs.jsonl"),
            llm=_mock_llm("Alice"),
        )
        store.handle_email("alice@gmail.com", "subscribe", "add me")
        assert store._notes.read_user_name("alice@gmail.com") == "Alice"

    def test_read_user_name_missing_file(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.jsonl"))
        assert store._notes.read_user_name("nobody@example.com") is None

    def test_unsubscribe_reads_stored_name(self, tmp_path: Path):
        sender_mock = Mock()
        llm_mock = _mock_llm("Alice")
        store = SubscriberStore(
            path=str(tmp_path / "subs.jsonl"),
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

    def test_subscribe_stores_name_in_subscriber(self, tmp_path: Path):
        store = SubscriberStore(
            path=str(tmp_path / "subs.jsonl"),
            llm=_mock_llm("Charlie"),
        )
        store.handle_email("charlie@example.com", "subscribe", "add me")
        sub = store.get("charlie@example.com")
        assert sub is not None
        assert sub.name == "Charlie"


class TestHandleEmailExtended:
    def test_no_sender_no_confirmation(self, tmp_path: Path):
        store = SubscriberStore(path=str(tmp_path / "subs.jsonl"))
        store.handle_email("alice@example.com", "subscribe", "add me")
        assert len(store.list()) == 1
        assert store.list()[0].email == "alice@example.com"

    def test_no_match_no_note_no_confirmation(self, tmp_path: Path):
        sender_mock = Mock()
        store = SubscriberStore(
            path=str(tmp_path / "subs.jsonl"),
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
