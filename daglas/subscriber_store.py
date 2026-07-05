from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import daglas.config
from daglas.email_sender_queue import EmailSenderQueue, MailItem
from daglas.lesson.formatter import Email
from typing import Any
from daglas.user_note_store import UserNoteStore

logger = logging.getLogger(__name__)


@dataclass
class Subscriber:
    email: str
    name: str = ""
    level: str = ""
    joined_at: str = ""
    vocab_count: int = 0

    def to_dict(self) -> dict:
        return {
            "email": self.email,
            "name": self.name,
            "level": self.level,
            "joined_at": self.joined_at,
            "vocab_count": self.vocab_count,
        }

    @staticmethod
    def from_dict(data: dict) -> Subscriber:
        return Subscriber(
            email=data.get("email", ""),
            name=data.get("name", ""),
            level=data.get("level", ""),
            joined_at=data.get("joined_at", ""),
            vocab_count=data.get("vocab_count", 0),
        )


def _read_prompt(name: str) -> str:
    cfg = daglas.config.config
    prompts_dir = Path(cfg.prompts_dir) if cfg is not None else Path("prompts")
    path = prompts_dir / name
    if path.is_file():
        return path.read_text().strip()
    logger.warning("Prompt file not found: %s", path)
    return ""


def _name_extraction_prompts() -> tuple[str, str]:
    system = _read_prompt("name_extraction_system.md")
    user_template = _read_prompt("name_extraction_user.md")
    if not system or not user_template:
        system = (
            "You extract names from email messages. Respond with ONLY the "
            "person's first name \u2014 nothing else, no punctuation, "
            "no explanation. If no name is found, respond with exactly: NONE"
        )
        user_template = "Subject: {subject}\nBody: {body}\nFrom: {sender}\n\nWhat is the sender's first name?"
    return system, user_template


WELCOME_SUBJECT = "Welcome to Dagl\u00e4sa! / V\u00e4lkommen till Dagl\u00e4sa!"
WELCOME_TEXT = """Hi {name}!

Welcome to Dagl\u00e4sa \u2014 your daily Swedish reading practice.

How it works:

\u2022 Each morning I scan news from {source_count} Swedish sources:
  {source_list}
\u2022 From all articles, I pick the most relevant one for your lesson.
\u2022 Articles are collected at {fetch_time}.
\u2022 Your lesson is created and delivered at {send_time}.

Each lesson includes the Swedish article text with simple English translations, key vocabulary, grammar breakdowns, and pronunciation guides.

You will receive your first lesson at {send_time} tomorrow.

---

Hej {name}!

V\u00e4lkommen till Dagl\u00e4sa \u2014 din dagliga svenska l\u00e4str\u00e4ning.

S\u00e5 h\u00e4r fungerar det:

\u2022 Varje morgon skannar jag nyheter fr\u00e5n {source_count} svenska k\u00e4llor:
  {source_list}
\u2022 Fr\u00e5n alla artiklar v\u00e4ljer jag den mest relevanta f\u00f6r din lektion.
\u2022 Artiklar samlas in kl. {fetch_time}.
\u2022 Din lektion skapas och levereras kl. {send_time}.

Varje lektion inneh\u00e5ller den svenska artikeltexten med enkla engelska \u00f6vers\u00e4ttningar, viktiga ord, grammatikgenomg\u00e5ng och uttalsguide.

Du f\u00e5r din f\u00f6rsta lektion kl. {send_time} i morgon.

Hej d\u00e5!
Dagl\u00e4sateamet"""

UNSUBSCRIBE_SUBJECT = (
    "Unsubscribed from Dagl\u00e4sa / Avprenumererad fr\u00e5n Dagl\u00e4sa"
)
UNSUBSCRIBE_TEXT = """Hi {name}!

You have been successfully unsubscribed from Dagl\u00e4sa.

You will no longer receive daily Swedish lessons.

If this was a mistake, simply reply with "subscribe" to rejoin.

---

Hej {name}!

Du har blivit avprenumererad fr\u00e5n Dagl\u00e4sa.

Du kommer inte l\u00e4ngre att f\u00e5 dagliga svenska lektioner.

Om detta var ett misstag, svara bara med "subscribe" f\u00f6r att b\u00f6rja igen.

Hej d\u00e5!
Dagl\u00e4sateamet"""


class SubscriberStore:
    def __init__(
        self,
        path: str | None = None,
        sender_queue: EmailSenderQueue | None = None,
        llm: Any | None = None,
        notes: UserNoteStore | None = None,
        welcome_template: Email | None = None,
        unsubscribe_template: Email | None = None,
    ):
        if path:
            self._path = Path(path)
        elif daglas.config.config is not None:
            data_dir = Path(daglas.config.config.data_dir)
            self._path = data_dir / "subscribers.jsonl"
        else:
            self._path = Path("data") / "subscribers.jsonl"

        self._data_dir = self._path.parent
        self._sender_queue = sender_queue
        self._llm = llm
        self._notes = notes or UserNoteStore(self._data_dir)

        self._migrate_from_txt()

        cfg = daglas.config.config
        send_time = cfg.send_time if cfg is not None else "07:00"
        fetch_time = cfg.fetch_time if cfg is not None else "06:00"

        sources = cfg.sources if cfg is not None else []
        source_count = len(sources)
        source_list = "\n  ".join(s.get("name", "?") for s in sources)

        if welcome_template is not None:
            self._welcome_template = welcome_template
        else:
            self._welcome_template = self._build_email(
                WELCOME_SUBJECT,
                WELCOME_TEXT,
                send_time=send_time,
                fetch_time=fetch_time,
                source_count=str(source_count),
                source_list=source_list,
            )

        if unsubscribe_template is not None:
            self._unsubscribe_template = unsubscribe_template
        else:
            self._unsubscribe_template = self._build_email(
                UNSUBSCRIBE_SUBJECT,
                UNSUBSCRIBE_TEXT,
                send_time=send_time,
            )

    def _migrate_from_txt(self) -> None:
        jsonl_path = self._path
        legacy_path = jsonl_path.with_suffix(".txt")
        if jsonl_path.is_file():
            return
        if not legacy_path.is_file():
            return
        logger.info("Migrating %s to %s", legacy_path.name, jsonl_path.name)
        lines = legacy_path.read_text().splitlines()
        emails = [line.strip() for line in lines if line.strip()]
        mtime = datetime.fromtimestamp(legacy_path.stat().st_mtime).isoformat()
        subs = [Subscriber(email=email, joined_at=mtime) for email in emails]
        self._write_all(subs)
        legacy_path.unlink()
        logger.info("Migrated %d subscribers from legacy file", len(subs))

    @staticmethod
    def _build_email(
        subject: str,
        text_template: str,
        send_time: str = "",
        fetch_time: str = "",
        source_count: str = "",
        source_list: str = "",
    ) -> Email:
        replacements = {
            "{send_time}": send_time,
            "{fetch_time}": fetch_time,
            "{source_count}": source_count,
            "{source_list}": source_list,
        }
        text_body = text_template
        for placeholder, value in replacements.items():
            text_body = text_body.replace(placeholder, value)
        html_body = _text_to_html(text_body)
        return Email(subject=subject, text_body=text_body, html_body=html_body)

    def _read_all(self) -> list[Subscriber]:
        if not self._path.is_file():
            return []
        result: list[Subscriber] = []
        for line in self._path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                result.append(Subscriber.from_dict(data))
            except json.JSONDecodeError:
                logger.warning("Skipping malformed JSONL line: %s", line[:80])
        return result

    def _write_all(self, subscribers: list[Subscriber]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lines = "\n".join(
            json.dumps(s.to_dict(), ensure_ascii=False) for s in subscribers
        )
        lines += "\n"
        fd, tmp = tempfile.mkstemp(
            dir=self._path.parent, prefix="subscribers_", suffix=".jsonl"
        )
        try:
            with open(fd, "w") as f:
                f.write(lines)
            tmp_path = Path(tmp)
            tmp_path.replace(self._path)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise

    def list(self) -> list[Subscriber]:
        return self._read_all()

    def get(self, email: str) -> Subscriber | None:
        email = email.strip()
        for sub in self._read_all():
            if sub.email == email:
                return sub
        return None

    def add(
        self,
        email: str,
        name: str = "",
        level: str = "",
        vocab_count: int = 0,
    ) -> Subscriber:
        email = email.strip()
        current = self._read_all()
        existing = next((s for s in current if s.email == email), None)
        now = datetime.now().isoformat()
        if existing:
            existing.name = name or existing.name
            existing.level = level or existing.level
            existing.vocab_count = vocab_count or existing.vocab_count
            self._write_all(current)
            return existing
        sub = Subscriber(
            email=email,
            name=name,
            level=level,
            joined_at=now,
            vocab_count=vocab_count,
        )
        current.append(sub)
        self._write_all(current)
        return sub

    def remove(self, email: str) -> None:
        email = email.strip()
        if not email:
            return
        if not self._path.is_file():
            return
        current = self._read_all()
        filtered = [s for s in current if s.email != email]
        if len(filtered) == len(current):
            return
        self._write_all(filtered)

    def update(self, email: str, **kwargs) -> Subscriber | None:
        email = email.strip()
        current = self._read_all()
        for sub in current:
            if sub.email == email:
                if "name" in kwargs:
                    sub.name = kwargs["name"]
                if "level" in kwargs:
                    sub.level = kwargs["level"]
                if "vocab_count" in kwargs:
                    sub.vocab_count = kwargs["vocab_count"]
                self._write_all(current)
                return sub
        return None

    def _extract_user_name(self, subject: str, body: str, sender: str) -> str:
        if self._llm is None:
            return sender.split("@")[0].title()
        system, user_template = _name_extraction_prompts()
        prompt = user_template.format(subject=subject, body=body, sender=sender)
        result = self._llm.prompt(system, prompt)
        if result is None:
            return sender.split("@")[0].title()
        result = result.strip()
        if not result or result.upper() == "NONE":
            return sender.split("@")[0].title()
        return result.title()

    def _send_confirmation(self, email: str, action: str, user_name: str) -> None:
        if self._sender_queue is None:
            logger.info("Confirmation: skipped (no EmailSenderQueue configured)")
            return
        template = (
            self._welcome_template
            if action == "subscribe"
            else self._unsubscribe_template
        )
        text_body = template.text_body.replace("{name}", user_name)
        html_body = template.html_body.replace("{name}", user_name)
        self._sender_queue.push(
            MailItem(
                to=[email],
                subject=template.subject,
                text_body=text_body,
                html_body=html_body,
                send_at="immediate",
            )
        )
        logger.info("Confirmation: queued for=%s action=%s", email, action)

    def handle_email(self, sender: str, subject: str, body: str) -> None:
        text = (subject + " " + body).lower()
        if "unsubscribe" in text:
            self.remove(sender)
            user_name = self._notes.read_user_name(sender) or self._extract_user_name(
                subject, body, sender
            )
            self._notes.save_received(sender, body, user_name)
            self._send_confirmation(sender, "unsubscribe", user_name)
            logger.info("Action: UNSUBSCRIBE sender=%s name=%s", sender, user_name)
        elif "subscribe" in text:
            user_name = self._extract_user_name(subject, body, sender)
            self.add(sender, name=user_name)
            self._notes.save_received(sender, body, user_name)
            self._send_confirmation(sender, "subscribe", user_name)
            logger.info("Action: SUBSCRIBE sender=%s name=%s", sender, user_name)
        else:
            logger.info("Action: NO_MATCH sender=%s subject=%s", sender, subject)


def _text_to_html(text: str) -> str:
    parts = [
        "<!DOCTYPE html>",
        "<html>",
        '<head><meta charset="utf-8"></head>',
        '<body style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px;">',
        '<div style="background:#f9f9f9;border-radius:8px;padding:24px;">',
    ]
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            parts.append("<br>")
        elif line == "---":
            parts.append("<hr>")
        else:
            parts.append(f"<p>{line}</p>")
    parts.append("</div></body></html>")
    return "\n".join(parts)
