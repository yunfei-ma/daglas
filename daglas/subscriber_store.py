from __future__ import annotations

import logging
from pathlib import Path

import daglas.config
from daglas.email_sender_queue import EmailSenderQueue, MailItem
from daglas.lesson.formatter import Email
from daglas.lesson.llm import LlmProvider
from daglas.user_note_store import UserNoteStore

logger = logging.getLogger(__name__)


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
        llm: LlmProvider | None = None,
        notes: UserNoteStore | None = None,
        welcome_template: Email | None = None,
        unsubscribe_template: Email | None = None,
    ):
        if path:
            self._path = Path(path)
        elif daglas.config.config is not None:
            data_dir = Path(daglas.config.config.data_dir)
            self._path = data_dir / "subscribers.txt"
        else:
            self._path = Path("data") / "subscribers.txt"

        self._data_dir = self._path.parent
        self._sender_queue = sender_queue
        self._llm = llm
        self._notes = notes or UserNoteStore(self._data_dir)

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

    def _read_all(self) -> list[str]:
        if not self._path.is_file():
            return []
        lines = self._path.read_text().splitlines()
        return [line.strip() for line in lines if line.strip()]

    def _write_all(self, lines: list[str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text("\n".join(lines) + "\n")

    def list(self) -> list[str]:
        return self._read_all()

    def add(self, email: str) -> None:
        email = email.strip()
        if not email:
            return
        current = self._read_all()
        if email in current:
            return
        current.append(email)
        self._write_all(current)

    def remove(self, email: str) -> None:
        email = email.strip()
        if not email:
            return
        if not self._path.is_file():
            return
        current = self._read_all()
        if email not in current:
            return
        current = [e for e in current if e != email]
        self._write_all(current)

    def _extract_user_name(self, subject: str, body: str, sender: str) -> str:
        if self._llm is None:
            return sender.split("@")[0].title()
        system, user_template = _name_extraction_prompts()
        prompt = user_template.format(subject=subject, body=body, sender=sender)
        result = self._llm.prompt(system, prompt)
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
            self.add(sender)
            user_name = self._extract_user_name(subject, body, sender)
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
