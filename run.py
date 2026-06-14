from __future__ import annotations

import argparse
import logging
import sys
import time
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


def _wire_inbound_pipeline(cfg, sender_queue):
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


def _load_saved_lesson() -> Email | None:
    md_path = Path("output") / "lesson.md"
    if not md_path.is_file():
        return None
    lesson_text = md_path.read_text()
    return format_email(lesson_text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dagläs — Daily Swedish Lesson")
    parser.add_argument(
        "--dry-run", action="store_true", help="Generate lesson without calling LLM"
    )
    parser.add_argument(
        "--fetch-only",
        action="store_true",
        help="Only fetch context, skip lesson generation",
    )
    parser.add_argument(
        "--generate-only",
        action="store_true",
        help="Generate lesson from existing context, skip send and fetch",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Send the lesson to subscribers",
    )
    parser.add_argument(
        "--html", action="store_true", help="Also generate HTML version"
    )
    parser.add_argument(
        "--max-articles", type=int, default=3, help="Max articles to fetch (default: 3)"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="[%(name)s] %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    daglas_config.config = load_config()
    cfg = daglas_config.config

    sender_queue = EmailSenderQueue()
    sender_queue.start()

    if cfg.imap_host:
        receiver = _wire_inbound_pipeline(cfg, sender_queue)
        count = receiver.check_once()
        logger = logging.getLogger("run")
        logger.info("Inbound: %d email(s) processed", count)
    else:
        logger = logging.getLogger("run")

    if not cfg.llm_endpoint and not args.fetch_only and not args.send:
        print("ERROR: llm_endpoint not configured in config.yaml")
        sys.exit(1)

    pool = ContextPool()

    if args.send and not args.fetch_only and not args.generate_only:
        email = _load_saved_lesson()
        if email is not None:
            _queue_lesson(email, "immediate", sender_queue)
            print("Waiting for queue to dispatch...")
            time.sleep(30)
            sender_queue.stop()
            return

    if not args.generate_only:
        if cfg.sources:
            print(f"Fetching context from {len(cfg.sources)} source(s)...")
            result = fetch_context(cfg.sources, pool, max_articles=args.max_articles)
            for err in result.errors:
                print(f"  WARN: {err}")
            print(f"Fetched {len(result.articles)} article(s)")
        else:
            print("No sources configured in config.yaml")

    if args.fetch_only:
        return

    articles = pool.retrieve_articles()
    if not articles:
        print("No articles in context pool.")
        sys.exit(1)

    provider = create_provider(
        endpoint=cfg.llm_endpoint,
        model=cfg.llm_model,
        api_key=cfg.llm_api_key,
    )

    lesson_text = generate_lesson(provider, articles, dry_run=args.dry_run)

    if args.dry_run:
        print("Dry-run mode — no LLM call made.")
        print(f"Would prompt with {len(articles)} article(s)")
        return

    if not lesson_text:
        print("ERROR: lesson generation returned empty result")
        sys.exit(1)

    email = format_email(lesson_text)

    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    md_path = out_dir / "lesson.md"
    md_path.write_text(email.text_body)
    print(f"Saved to {md_path}")

    if args.html:
        html_path = out_dir / "lesson.html"
        html_path.write_text(email.html_body)
        print(f"Saved to {html_path}")
    else:
        print("(use --html to also generate lesson.html)")

    if args.generate_only:
        sender_queue.stop()
        return

    if args.send:
        _queue_lesson(email, "immediate", sender_queue)
        print("Waiting for queue to dispatch...")
        time.sleep(30)
        sender_queue.stop()
    else:
        _queue_lesson(email, cfg.send_time, sender_queue)
        sender_queue.stop()
        print("Lesson queued for scheduled dispatch.")


if __name__ == "__main__":
    main()
