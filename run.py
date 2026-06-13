import argparse
import sys
from pathlib import Path

from daglas import config as daglas_config
from daglas.config import load_config
from daglas.context_fetcher import fetch_context
from daglas.context_pool import ContextPool
from daglas.email_sender import SmtpSender
from daglas.lesson.formatter import Email, format_email
from daglas.lesson.generator import generate_lesson
from daglas.lesson.llm import create_provider
from daglas.subscriber_store import SubscriberStore


def _send_email(email: Email) -> None:
    cfg = daglas_config.config
    store = SubscriberStore()
    recipients = store.list()
    if not recipients:
        print("No subscribers — skipping send.")
        return
    sender = SmtpSender(
        host=cfg.smtp_host,
        port=cfg.smtp_port,
        user=cfg.smtp_user,
        password=cfg.smtp_password,
        from_address=cfg.from_address,
    )
    result = sender.send(email, recipients)
    print(f"Sent: {result.success_count} ok, {result.failure_count} failed")
    for err in result.errors:
        print(f"  Send error: {err}")


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

    daglas_config.config = load_config()
    cfg = daglas_config.config

    if not cfg.llm_endpoint and not args.fetch_only and not args.send:
        print("ERROR: llm_endpoint not configured in config.yaml")
        sys.exit(1)

    pool = ContextPool()

    # Standalone send: load saved lesson and send without generating
    if args.send and not args.fetch_only and not args.generate_only:
        email = _load_saved_lesson()
        if email is not None:
            _send_email(email)
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
        return

    if args.send:
        _send_email(email)


if __name__ == "__main__":
    main()
