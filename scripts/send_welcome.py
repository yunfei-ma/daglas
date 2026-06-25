#!/usr/bin/env python3
"""Send welcome email to all existing subscribers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daglas import config as daglas_config
from daglas.config import load_config
from daglas.email_sender_queue import EmailSenderQueue, MailItem
from daglas.subscriber_store import SubscriberStore


def main() -> int:
    daglas_config.config = load_config()

    sender_queue = EmailSenderQueue()
    sender_queue.start()

    store = SubscriberStore(sender_queue=sender_queue)
    subscribers = store.list()

    if not subscribers:
        print("No subscribers.")
        sender_queue.stop()
        return 0

    template = store._welcome_template

    for email in subscribers:
        name = store._read_user_name(email) or email.split("@")[0].title()
        text_body = template.text_body.replace("{name}", name)
        html_body = template.html_body.replace("{name}", name)
        sender_queue.push(
            MailItem(
                to=[email],
                subject=template.subject,
                text_body=text_body,
                html_body=html_body,
                send_at="immediate",
            )
        )
        print(f"Queued welcome for {email} (name={name})")

    sender_queue.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
