from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from daglas import config as daglas_config
from daglas.config import load_config
from daglas.context_fetcher import fetch_context
from daglas.context_pool import ContextPool
from daglas.email_sender_queue import EmailSenderQueue, MailItem
from daglas.heartbeat import Heartbeat
from daglas.lesson.formatter import format_email
from daglas.lesson.generator import generate_lesson
from daglas.lesson.llm import create_llm
from daglas.subscriber_store import SubscriberStore

logger = logging.getLogger(__name__)


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


def _send_admin_alert(
    sender_queue: EmailSenderQueue,
    cfg,
    title: str,
    details: str,
) -> None:
    if not cfg or not cfg.admin_email:
        return
    text = (
        f"Dagläs Alert — {title}\n\n"
        f"Time: {datetime.now(timezone.utc).isoformat()}\n"
        f"{details}\n"
    )
    sender_queue.push(
        MailItem(
            to=[cfg.admin_email],
            subject=f"Dagläs: {title}",
            text_body=text,
            html_body=f"<pre>{text}</pre>",
            send_at="immediate",
        )
    )


def _run_loop(heartbeat: Heartbeat, actions: dict[str, Callable[[], None]]) -> None:
    while not heartbeat._shutdown.is_set():
        for name in heartbeat.tick():
            handler = actions.get(name)
            if handler is None:
                logger.warning("Unknown scheduled action: %s", name)
                continue
            try:
                handler()
                heartbeat.set_complete(name)
            except Exception:
                logger.exception("Action %s failed — retry on next tick", name)
        heartbeat.run_due_pollers()
        heartbeat._shutdown.wait(timeout=1)


def _make_fetch_action(cfg, sender_queue) -> Callable[[], None]:
    def fetch() -> None:
        pool = ContextPool()
        pool.clear()
        fetch_context(cfg.sources, pool)
        articles = pool.retrieve_articles()
        best = max(articles, key=lambda a: a.get("score") or 0)
        llm = create_llm(cfg)

        store = SubscriberStore()
        subscribers = store.list()
        if not subscribers:
            logger.info("No subscribers — skipping lesson dispatch.")
            return

        groups: dict[tuple[str, int], list[str]] = {}
        for sub in subscribers:
            group_level = sub.level or (cfg.lesson_level if cfg else "beginner")
            group_vcount = sub.vocab_count or (cfg.vocab_count if cfg else 5)
            key = (group_level, group_vcount)
            if key not in groups:
                groups[key] = []
            groups[key].append(sub.email)

        for (group_level, group_vcount), emails in groups.items():
            lesson_text = generate_lesson(
                llm,
                [best],
                level=group_level,
                vocab_count=group_vcount,
            )
            if not lesson_text:
                logger.error(
                    "Lesson generation returned empty for group level=%s vcount=%d",
                    group_level,
                    group_vcount,
                )
                article_info = "\n".join(
                    a.get("title", "") for a in [best] if a.get("title")
                )
                _send_admin_alert(
                    sender_queue,
                    cfg,
                    "Lesson generation failed",
                    f"Group: level={group_level} vcount={group_vcount}\n"
                    f"Articles:\n{article_info}",
                )
                continue

            email = format_email(lesson_text)
            subject = best.get("title") or email.subject
            recipients = emails
            if cfg.debug_mode:
                subject = f"[debug] {subject}"
                recipients = [cfg.admin_email] if cfg.admin_email else []
            sender_queue.push(
                MailItem(
                    to=recipients,
                    subject=subject,
                    text_body=email.text_body,
                    html_body=email.html_body,
                    send_at="immediate",
                )
            )
            logger.info(
                "Lesson queued: group=(level=%s vcount=%d) recipients=%d%s",
                group_level,
                group_vcount,
                len(recipients),
                " [debug]" if cfg.debug_mode else "",
            )

    return fetch


def main() -> None:
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

    cfg = daglas_config.config
    heartbeat = Heartbeat()
    sender_queue = EmailSenderQueue()

    if cfg.imap_host:
        receiver = _wire_email_receiver(cfg, sender_queue)
        heartbeat.add_poller("imap", cfg.email_receiver_poll_interval, receiver.poll)

    heartbeat.add_poller(
        "sender_queue",
        cfg.email_sender_immediate_empty_interval,
        sender_queue.dispatch_due,
    )

    actions: dict[str, Callable[[], None]] = {
        "fetch": _make_fetch_action(cfg, sender_queue),
        "send": sender_queue.dispatch_due,
    }

    logger.info("Dagläs heartbeat started (magic_string=%s)", cfg.magic_string)
    try:
        _run_loop(heartbeat, actions)
    except KeyboardInterrupt:
        heartbeat.stop()
        logger.info("Dagläs heartbeat stopped")


if __name__ == "__main__":
    main()
