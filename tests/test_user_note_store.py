from uuid import uuid4

from daglas.user_note_store import UserNoteStore


def test_save_received_creates_file(tmp_path):
    store = UserNoteStore(tmp_path)
    store.save_received("alice@example.com", "Hello!", "Alice")
    path = tmp_path / "notes" / "alice_example_com.txt"
    assert path.is_file()
    content = path.read_text()
    assert "Email: alice@example.com" in content
    assert "Name: Alice" in content
    assert "Type: received" in content
    assert "Hello!" in content


def test_save_received_prepends(tmp_path):
    store = UserNoteStore(tmp_path)
    store.save_received("alice@example.com", "First", "Alice")
    store.save_received("alice@example.com", "Second")
    path = tmp_path / "notes" / "alice_example_com.txt"
    content = path.read_text()
    assert content.count("Type: received") == 2
    assert "Email:" in content.split("Type:")[0]
    latest_idx = content.index("Second")
    first_idx = content.index("First")
    assert latest_idx < first_idx, "latest entry should appear before older entry"


def test_save_received_without_name(tmp_path):
    store = UserNoteStore(tmp_path)
    store.save_received("alice@example.com", "Hello!")
    path = tmp_path / "notes" / "alice_example_com.txt"
    assert path.is_file()
    content = path.read_text()
    assert "Email:" not in content
    assert "Name:" not in content
    assert "Hello!" in content


def test_save_sent_writes_to_each_recipient(tmp_path):
    store = UserNoteStore(tmp_path)
    rid = uuid4()
    store.save_sent(
        to=["alice@example.com", "bob@example.com"],
        subject="Lesson",
        text_body="Hej!",
        html_body="<p>Hej!</p>",
        send_at="immediate",
        request_id=rid,
    )
    alice_path = tmp_path / "notes" / "alice_example_com.txt"
    bob_path = tmp_path / "notes" / "bob_example_com.txt"
    assert alice_path.is_file()
    assert bob_path.is_file()
    assert "Type: sent" in alice_path.read_text()
    assert "Type: sent" in bob_path.read_text()


def test_save_sent_entry_format(tmp_path):
    store = UserNoteStore(tmp_path)
    rid = uuid4()
    store.save_sent(
        to=["alice@example.com"],
        subject="Daily Lesson",
        text_body="Hej och välkommen!",
        html_body="",
        send_at="immediate",
        request_id=rid,
    )
    content = (tmp_path / "notes" / "alice_example_com.txt").read_text()
    assert "Date: " in content
    assert "Type: sent" in content
    assert "Subject: Daily Lesson" in content
    assert "Send At: immediate" in content
    assert f"Request ID: {rid}" in content
    assert "--------------------------------" in content
    assert "Hej och välkommen!" in content


def test_save_sent_prepends_to_existing(tmp_path):
    store = UserNoteStore(tmp_path)
    store.save_received("alice@example.com", "Received note", "Alice")
    store.save_sent(
        to=["alice@example.com"],
        subject="Sent Lesson",
        text_body="Hej!",
        html_body="",
        send_at="immediate",
        request_id=uuid4(),
    )
    content = (tmp_path / "notes" / "alice_example_com.txt").read_text()
    assert content.count("Type: received") == 1
    assert content.count("Type: sent") == 1
    assert content.index("Type: sent") < content.index("Type: received")


def test_read_user_name_found(tmp_path):
    store = UserNoteStore(tmp_path)
    store.save_received("alice@example.com", "Hej!", "Alice")
    assert store.read_user_name("alice@example.com") == "Alice"


def test_read_user_name_missing(tmp_path):
    store = UserNoteStore(tmp_path)
    store.save_received("alice@example.com", "Hej!")
    assert store.read_user_name("alice@example.com") is None


def test_read_user_name_no_file(tmp_path):
    store = UserNoteStore(tmp_path)
    assert store.read_user_name("nobody@example.com") is None


def test_email_to_filename(tmp_path):
    assert UserNoteStore.email_to_filename("alice@gmail.com") == "alice_gmail_com"


def test_email_to_filename_special_chars(tmp_path):
    assert UserNoteStore.email_to_filename("a.b@c-d.com") == "a_b_c-d_com"
    assert UserNoteStore.email_to_filename("x@y.z") == "x_y_z"


def test_prepend_order_without_header(tmp_path):
    store = UserNoteStore(tmp_path)
    store.save_sent(
        to=["a@b.com"],
        subject="First",
        text_body="old",
        html_body="",
        send_at="immediate",
        request_id=uuid4(),
    )
    store.save_sent(
        to=["a@b.com"],
        subject="Second",
        text_body="new",
        html_body="",
        send_at="immediate",
        request_id=uuid4(),
    )
    content = (tmp_path / "notes" / "a_b_com.txt").read_text()
    assert content.index("Subject: Second") < content.index("Subject: First")


def test_save_sent_creates_dir(tmp_path):
    store = UserNoteStore(tmp_path / "nested" / "dir")
    store.save_sent(
        to=["a@b.com"],
        subject="S",
        text_body="T",
        html_body="",
        send_at="immediate",
        request_id=uuid4(),
    )
    assert (tmp_path / "nested" / "dir" / "notes" / "a_b_com.txt").is_file()
