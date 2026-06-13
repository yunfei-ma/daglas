from pathlib import Path

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
