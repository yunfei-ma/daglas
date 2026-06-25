from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daglas import config as daglas_config
from daglas.config import load_config
from daglas.context_fetcher import fetch_context
from daglas.context_pool import ContextPool
from daglas.email_sender_queue import EmailSenderQueue, MailItem
from daglas.lesson.formatter import format_email
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

    receiver = EmailReceiver(queue)
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


def _queue_lessons(
    provider,
    articles: list[dict],
    send_at: str,
    sender_queue: EmailSenderQueue,
) -> None:
    cfg = daglas_config.config
    store = SubscriberStore()
    subscribers = store.list()
    if not subscribers:
        print("No subscribers — skipping lesson dispatch.")
        return

    resolved = _resolve_send_time(send_at)

    groups: dict[tuple[str, int], list[str]] = defaultdict(list)
    for sub in subscribers:
        group_level = sub.level or (cfg.lesson_level if cfg else "beginner")
        group_vcount = sub.vocab_count or (cfg.vocab_count if cfg else 5)
        groups[(group_level, group_vcount)].append(sub.email)

    logger = logging.getLogger("run")
    for (group_level, group_vcount), emails in groups.items():
        lesson_text = generate_lesson(
            provider,
            articles,
            level=group_level,
            vocab_count=group_vcount,
        )
        if not lesson_text:
            logger.error(
                "Lesson generation returned empty for group level=%s vcount=%d",
                group_level,
                group_vcount,
            )
            continue

        email = format_email(lesson_text)
        sender_queue.push(
            MailItem(
                to=emails,
                subject=email.subject,
                text_body=email.text_body,
                html_body=email.html_body,
                send_at=resolved,
            )
        )
        logger.info(
            "Lesson queued: group=(level=%s vcount=%d) recipients=%d send_at=%s",
            group_level,
            group_vcount,
            len(emails),
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
    pool.clear()

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

    best = max(articles, key=lambda a: a.get("score") or 0)
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    sel_path = out_dir / "selected.txt"
    sel_path.write_text(
        f"Title: {best.get('title', '')}\nURL: {best.get('url', '')}\n\n{best.get('body', '')}"
    )
    print(f"Selected article: {best.get('title', '')[:70]}")

    provider = create_provider(
        endpoint=cfg.llm_endpoint,
        model=cfg.llm_model,
        api_key=cfg.llm_api_key,
    )

    md_path = out_dir / "lesson.md"
    first_group = True
    store = SubscriberStore()
    subscribers = store.list()
    if not subscribers:
        print("No subscribers — skipping lesson dispatch.")
        sender_queue.stop()
        return

    groups: dict[tuple[str, int], list[str]] = defaultdict(list)
    for sub in subscribers:
        group_level = sub.level or (cfg.lesson_level if cfg else "beginner")
        group_vcount = sub.vocab_count or (cfg.vocab_count if cfg else 5)
        groups[(group_level, group_vcount)].append(sub.email)

    resolved = _resolve_send_time(cfg.send_time)
    for (group_level, group_vcount), emails in groups.items():
        lesson_text = generate_lesson(
            provider,
            [best],
            level=group_level,
            vocab_count=group_vcount,
        )
        if not lesson_text:
            print("ERROR: lesson generation returned empty result")
            continue

        email = format_email(lesson_text)

        if first_group:
            md_path.write_text(email.text_body)
            print(f"Saved to {md_path}")
            first_group = False

        sender_queue.push(
            MailItem(
                to=emails,
                subject=email.subject,
                text_body=email.text_body,
                html_body=email.html_body,
                send_at=resolved,
            )
        )
        print(
            f"Lesson queued for group (level={group_level} vcount={group_vcount}): "
            f"{len(emails)} recipient(s)"
        )

    sender_queue.stop()


def _run_persistent() -> None:
    cfg = daglas_config.config
    sender_queue = EmailSenderQueue()
    sender_queue.start()

    receiver = None
    if cfg.imap_host:
        receiver = _wire_email_receiver(cfg, sender_queue)
        receiver.start()

    _shutdown_event = threading.Event()
    print("Dagläs running. Press Ctrl+C to quit.")
    try:
        _shutdown_event.wait()
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

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]

    log_dir = Path.home() / "Library" / "Logs" / "daglas"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.TimedRotatingFileHandler(
        str(log_dir / "daglas.log"),
        when="midnight",
        backupCount=30,
    )
    handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        handlers=handlers,
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
