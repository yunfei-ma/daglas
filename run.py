from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daglas import config as daglas_config
from daglas.config import load_config
from daglas.context_fetcher import fetch_context
from daglas.context_pool import ContextPool
from daglas.email_sender_queue import EmailSenderQueue, SendRequest
from daglas.lesson.formatter import Email, format_email
from daglas.lesson.generator import generate_lesson
from daglas.lesson.llm import create_provider
from daglas.subscriber_store import SubscriberStore


def _wire_email_receiver(cfg, sender_queue):
    from daglas.email_queue import EmailQueue
    from daglas.email_receiver import EmailReceiver
    from daglas.email_processor import EmailProcessor

    queue = EmailQueue()
    processor = EmailProcessor(queue)
    store = SubscriberStore(sender_queue=sender_queue)
    processor.add_listener(store.handle_email)

    receiver = EmailReceiver(
        queue,
        imap_host=cfg.imap_host,
        imap_port=cfg.imap_port,
        imap_user=cfg.imap_user,
        imap_password=cfg.imap_password,
    )
    return receiver


def _resolve_send_time(time_str: str) -> str:
    if time_str == "immediate":
        return "immediate"
    try:
        hour, minute = time_str.split(":")
        now = datetime.now(timezone.utc)
        send_dt = now.replace(
            hour=int(hour), minute=int(minute), second=0, microsecond=0
        )
        if send_dt <= now:
            send_dt += timedelta(days=1)
        return send_dt.isoformat()
    except (ValueError, TypeError):
        logger = logging.getLogger("run")
        logger.warning("Invalid send_time=%r — falling back to immediate", time_str)
        return "immediate"


def _queue_lesson(lesson: Email, send_at: str, sender_queue: EmailSenderQueue) -> None:
    store = SubscriberStore()
    recipients = store.list()
    if not recipients:
        print("No subscribers — skipping lesson dispatch.")
        return
    resolved = _resolve_send_time(send_at)
    sender_queue.push(
        SendRequest(
            to=recipients,
            subject=lesson.subject,
            body=lesson.text_body,
            html_body=lesson.html_body,
            send_at=resolved,
        )
    )
    logger = logging.getLogger("run")
    logger.info(
        "Lesson queued: subject=%s recipients=%d send_at=%s",
        lesson.subject,
        len(recipients),
        resolved,
    )


def _run_generate() -> None:
    cfg = daglas_config.config
    sender_queue = EmailSenderQueue()
    sender_queue.start()

    if cfg.imap_host:
        receiver = _wire_email_receiver(cfg, sender_queue)
        count = receiver.check_once()
        logger = logging.getLogger("run")
        logger.info("EmailReceiver: processed %d email(s)", count)

    if not cfg.llm_endpoint:
        print("ERROR: llm_endpoint not configured in config.yaml")
        sys.exit(1)

    pool = ContextPool()

    if cfg.sources:
        print(f"Fetching context from {len(cfg.sources)} source(s)...")
        result = fetch_context(cfg.sources, pool)
        for err in result.errors:
            print(f"  WARN: {err}")
        print(f"Fetched {len(result.articles)} article(s)")
    else:
        print("No sources configured in config.yaml")

    articles = pool.retrieve_articles()
    if not articles:
        print("No articles in context pool.")
        sys.exit(1)

    provider = create_provider(
        endpoint=cfg.llm_endpoint,
        model=cfg.llm_model,
        api_key=cfg.llm_api_key,
    )

    lesson_text = generate_lesson(provider, articles)

    if not lesson_text:
        print("ERROR: lesson generation returned empty result")
        sys.exit(1)

    email = format_email(lesson_text)

    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    md_path = out_dir / "lesson.md"
    md_path.write_text(email.text_body)
    print(f"Saved to {md_path}")

    _queue_lesson(email, cfg.send_time, sender_queue)
    print("Lesson queued for scheduled dispatch.")

    sender_queue.stop()


def _run_persistent() -> None:
    cfg = daglas_config.config
    sender_queue = EmailSenderQueue()
    sender_queue.start()

    receiver = None
    if cfg.imap_host:
        receiver = _wire_email_receiver(cfg, sender_queue)
        receiver.start()

    print("Dagläs running. Press Ctrl+C or type q+Enter to quit.")
    try:
        while True:
            try:
                line = input()
                if line.strip().lower() == "q":
                    print("Shutting down...")
                    break
            except EOFError:
                break
            except KeyboardInterrupt:
                break
    except KeyboardInterrupt:
        print("\nShutting down...")

    if receiver:
        receiver.stop()
    sender_queue.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Dagläs — Daily Swedish Lesson")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Run full lesson lifecycle (fetch, generate, queue) and exit",
    )
    args = parser.parse_args()

    level = getattr(
        logging, os.environ.get("DAGLAS_LOG_LEVEL", "INFO").upper(), logging.INFO
    )
    if not isinstance(level, int):
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)-5s [%(name)s] %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("trafilatura").setLevel(logging.WARNING)
    daglas_config.config = load_config()

    if args.generate:
        _run_generate()
    else:
        _run_persistent()


if __name__ == "__main__":
    main()
