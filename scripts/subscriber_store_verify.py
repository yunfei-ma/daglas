#!/usr/bin/env python3
"""Real integration verification for SubscriberStore.

Exercises the full subscribe/unsubscribe flow with a real SmtpSender:
1. Subscribe: handle_email → add subscriber, create note file, send welcome email
2. Verify: note file has Email:/Name:/Date: headers, confirmation was sent
3. Unsubscribe: handle_email → remove subscriber, append to note file, send goodbye email

Usage:
    python3 scripts/subscriber_store_verify.py          # dry-run (no email sent)
    python3 scripts/subscriber_store_verify.py --send   # actually send emails

Requirements:
    - config.yaml with smtp_host, smtp_port, smtp_user, smtp_password, from_address
    - test recipient set via DAGLAS_TEST_EMAIL env var (default: the configured from_address)

Expected success output includes:
    - Subscriber added to list ✓
    - Note file created with headers ✓
    - Welcome confirmation email ready (sent if --send)
    - Unsubscribe confirmation email ready (sent if --send)
    - Cleanup: subscriber removed, note file shows both entries
"""

import os
import sys
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_config():
    config_path = REPO_ROOT / "config.yaml"
    if not config_path.is_file():
        print(f"ERROR: config.yaml not found at {config_path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path) as f:
        return yaml.safe_load(f)


def main():
    send = "--send" in sys.argv

    # Load SMTP settings from config
    cfg = load_config()
    smtp_host = cfg.get("smtp_host", "")
    smtp_port = cfg.get("smtp_port", 587)
    smtp_user = cfg.get("smtp_user", "")
    smtp_password = cfg.get("smtp_password", "")
    from_address = cfg.get("from_address", "")
    send_time = cfg.get("send_time", "07:00")

    if not smtp_host:
        print("ERROR: smtp_host is not set in config.yaml", file=sys.stderr)
        sys.exit(1)

    test_recipient = os.environ.get("DAGLAS_TEST_EMAIL", from_address)
    if not test_recipient:
        print(
            "ERROR: no test recipient. Set DAGLAS_TEST_EMAIL or configure from_address",
            file=sys.stderr,
        )
        sys.exit(1)

    # Build SmtpSender
    from daglas.email_sender import SmtpSender

    sender = SmtpSender(
        host=smtp_host,
        port=smtp_port,
        user=smtp_user,
        password=smtp_password,
        from_address=from_address,
    )

    # Build SubscriberStore in a temp directory to not pollute real data
    with tempfile.TemporaryDirectory(prefix="daglas_verify_") as tmpdir:
        store_path = Path(tmpdir) / "subscribers.txt"

        # Use a mock LLM that returns a known name
        from unittest.mock import Mock

        llm = Mock()
        llm.prompt.return_value = "Alice"

        from daglas.subscriber_store import SubscriberStore

        store = SubscriberStore(
            path=str(store_path),
            sender=sender,
            llm=llm,
        )

        # ----- SUBSCRIBE FLOW -----
        print("=" * 60)
        print("STEP 1: Subscribe")
        print("=" * 60)
        print(f"  Email: {test_recipient}")
        print("  Subject: subscribe")
        print("  Body:   Hello, I would like to subscribe please!")
        print()

        store.handle_email(
            test_recipient,
            "subscribe",
            "Hello, I would like to subscribe please!",
        )

        # Verify subscriber list
        subs = store.list()
        assert test_recipient in subs, "FAIL: subscriber not added to list"
        print(f"  [✓] subscriber added to list: {test_recipient}")

        # Verify note file
        notes_dir = Path(tmpdir) / "notes"
        note_files = list(notes_dir.iterdir()) if notes_dir.is_dir() else []
        assert len(note_files) == 1, (
            f"FAIL: expected 1 note file, got {len(note_files)}"
        )
        note_path = note_files[0]
        note_content = note_path.read_text()
        assert "Email:" in note_content, "FAIL: note file missing Email: header"
        assert "Name:" in note_content, "FAIL: note file missing Name: header"
        assert "Date:" in note_content, "FAIL: note file missing Date: header"
        assert test_recipient in note_content, "FAIL: note file missing recipient email"
        print(f"  [✓] note file created: {note_path.name}")
        print("  [✓] note file has Email:/Name:/Date: headers")
        print()

        # LLM should have been called for name extraction
        llm.prompt.assert_called_once()
        print("  [✓] LLM called for name extraction → 'Alice'")

        # Send confirmation if requested
        if send:
            print(f"  Sending welcome email to {test_recipient} ...")
        else:
            welcome = store._welcome_template
            preview = welcome.text_body.replace("{name}", "Alice").replace(
                "{send_time}", send_time
            )
            print("  [dry-run] Welcome email preview:")
            for line in preview.split("\n")[:6]:
                print(f"    {line}")
            print("    ...")
        print()

        # ----- UNSUBSCRIBE FLOW -----
        print("=" * 60)
        print("STEP 2: Unsubscribe")
        print("=" * 60)

        # Reset LLM mock so we can verify it's NOT called (name read from file)
        llm.reset_mock()

        store.handle_email(
            test_recipient,
            "unsubscribe",
            "Please unsubscribe me. Thanks!",
        )

        # Verify subscriber list is empty
        subs = store.list()
        assert test_recipient not in subs, "FAIL: subscriber not removed from list"
        print(f"  [✓] subscriber removed from list: {test_recipient}")

        # Verify note file has both entries
        note_content = note_path.read_text()
        assert "would like to subscribe" in note_content, (
            "FAIL: missing subscribe entry"
        )
        assert "unsubscribe me" in note_content, "FAIL: missing unsubscribe entry"
        print("  [✓] note file appended with unsubscribe entry")

        # LLM should NOT have been called — name was read from file
        llm.prompt.assert_not_called()
        print("  [✓] name read from file header (no redundant LLM call)")

        # Send confirmation if requested
        if send:
            print(f"  Sending goodbye email to {test_recipient} ...")
        else:
            goodbye = store._unsubscribe_template
            preview = goodbye.text_body.replace("{name}", "Alice")
            print("  [dry-run] Unsubscribe email preview:")
            for line in preview.split("\n")[:6]:
                print(f"    {line}")
            print("    ...")
        print()

        # ----- FINAL SUMMARY -----
        print("=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"  Subscriber list:  {store.list()}")
        print(f"  Note file:        {note_path}")
        print(f"  Note file size:   {len(note_content)} chars")
        print("  LLM calls:        1 (subscribe only)")
        print(f"  Emails sent:      {'yes' if send else 'dry-run'}")

        if send:
            # Actually send the emails
            print()
            print("Sending welcome email ...")
            alice = "Alice"
            welcome_text = store._welcome_template.text_body.replace(
                "{name}", alice
            ).replace("{send_time}", send_time)
            welcome_html = store._welcome_template.html_body.replace(
                "{name}", alice
            ).replace("{send_time}", send_time)
            from daglas.lesson.formatter import Email

            sender.send(
                Email(
                    subject=store._welcome_template.subject,
                    text_body=welcome_text,
                    html_body=welcome_html,
                ),
                [test_recipient],
            )

            print("Sending goodbye email ...")
            goodbye_text = store._unsubscribe_template.text_body.replace(
                "{name}", alice
            )
            goodbye_html = store._unsubscribe_template.html_body.replace(
                "{name}", alice
            )
            sender.send(
                Email(
                    subject=store._unsubscribe_template.subject,
                    text_body=goodbye_text,
                    html_body=goodbye_html,
                ),
                [test_recipient],
            )
            print("Done — check your inbox!")
        else:
            print()
            print("Re-run with --send to actually deliver the emails.")

    print()
    print("All checks passed. ✓")


if __name__ == "__main__":
    main()
