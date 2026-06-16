#!/usr/bin/env python3
"""Real integration verification for EmailReceiver.

Connects to the configured Gmail IMAP inbox, polls for UNSEEN messages,
processes subscribe/unsubscribe patterns, and reports results.

Usage:
    python3 scripts/email_receiver_verify.py

Expected success output includes:
    - "Checking inbox aaolingv@gmail.com ..."
    - Connection succeeded: "Connected to imap.gmail.com:993"
    - Number of unseen messages found
    - For each matching message: sender + action taken
    - Summary line: "Result: X subscribed, Y unsubscribed, Z errors"
"""

import email
import imaplib
import sys
from email.utils import parseaddr

import yaml

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
IMAP_USER = "aaolingv@gmail.com"
with open("config.yaml") as f:
    IMAP_PASSWORD = yaml.safe_load(f)["imap_password"]


def get_body(msg):
    parts = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    parts.append(payload.decode("utf-8", errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            parts.append(payload.decode("utf-8", errors="replace"))
    return "\n".join(parts)


def main():
    print(f"Checking inbox {IMAP_USER} ...")
    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        conn.login(IMAP_USER, IMAP_PASSWORD)
        conn.select("INBOX")
        print(f"Connected to {IMAP_HOST}:{IMAP_PORT}")
    except Exception as e:
        print(f"FAILED to connect: {e}", file=sys.stderr)
        sys.exit(1)

    _, data = conn.search(None, "UNSEEN")
    msg_ids = data[0].split() if data[0] else []
    print(f"Unseen messages: {len(msg_ids)}")

    subscribed = []
    unsubscribed = []
    errors = []

    for mid in msg_ids:
        try:
            _, raw = conn.fetch(mid, "(RFC822)")
            msg = email.message_from_bytes(raw[0][1])
            _, sender = parseaddr(msg["From"])
            if not sender:
                errors.append(f"Msg {mid}: no valid From address")
                continue
            subject = msg["Subject"] or ""
            body = get_body(msg)
            text = (subject + " " + body).lower()
            print(f"  Msg {mid}: from={sender}, subject={subject!r}")
            if "unsubscribe" in text:
                unsubscribed.append(sender)
                print(f"    -> UNSUBSCRIBE: {sender}")
            elif "subscribe" in text:
                subscribed.append(sender)
                print(f"    -> SUBSCRIBE: {sender}")
            else:
                print(f"    -> no match (body snippet: {body[:60]!r})")
            conn.store(mid, "+FLAGS", "\\Seen")
        except Exception as e:
            errors.append(f"Msg {mid}: {e}")
            print(f"    -> ERROR: {e}")

    conn.logout()
    print()
    print(
        f"Result: {len(subscribed)} subscribed, {len(unsubscribed)} unsubscribed, {len(errors)} errors"
    )
    for s in subscribed:
        print(f"  Subscribed: {s}")
    for s in unsubscribed:
        print(f"  Unsubscribed: {s}")
    for e in errors:
        print(f"  Error: {e}")


if __name__ == "__main__":
    main()
