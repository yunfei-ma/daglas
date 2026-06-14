from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import daglas.config
from daglas.email_sender_queue import EmailSenderQueue, SendRequest
from daglas.lesson.formatter import Email
from daglas.lesson.llm import LlmProvider

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

Each morning you will receive a short Swedish news article or story with simple English explanations. This is not a grammar course \u2014 it is designed to help you build reading confidence by engaging with real Swedish content every day.

You will receive your first lesson at {send_time} tomorrow.
Lessons arrive daily at {send_time}.

---

Hej {name}!

V\u00e4lkommen till Dagl\u00e4sa \u2014 din dagliga svenska l\u00e4str\u00e4ning.

Varje morgon f\u00e5r du en kort svensk nyhetsartikel eller ber\u00e4ttelse med enkla engelska f\u00f6rklaringar. Det h\u00e4r \u00e4r ingen grammatikkurs \u2014 det \u00e4r utformat f\u00f6r att hj\u00e4lpa dig bygga l\u00e4sf\u00f6rst\u00e5else genom att ta del av verklig svensk text varje dag.

Du f\u00e5r din f\u00f6rsta lektion klockan {send_time} i morgon.
Lektionerna kommer varje dag klockan {send_time}.

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

        send_time = (
            daglas.config.config.send_time
            if daglas.config.config is not None
            else "07:00"
        )

        if welcome_template is not None:
            self._welcome_template = welcome_template
        else:
            self._welcome_template = self._build_email(
                WELCOME_SUBJECT, WELCOME_TEXT, send_time
            )

        if unsubscribe_template is not None:
            self._unsubscribe_template = unsubscribe_template
        else:
            self._unsubscribe_template = self._build_email(
                UNSUBSCRIBE_SUBJECT, UNSUBSCRIBE_TEXT, send_time
            )

    @staticmethod
    def _build_email(subject: str, text_template: str, send_time: str) -> Email:
        text_body = text_template.replace("{send_time}", send_time)
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

    @staticmethod
    def _email_to_filename(email: str) -> str:
        result = email.replace("@", "_").replace(".", "_")
        unsafe = '/\\:*?"<>|'
        for ch in unsafe:
            result = result.replace(ch, "_")
        return result

    def _read_user_name(self, email: str) -> str | None:
        note_path = self._data_dir / "notes" / f"{self._email_to_filename(email)}.txt"
        if not note_path.is_file():
            return None
        for line in note_path.read_text().splitlines():
            if line.startswith("Name: "):
                return line[6:]
        return None

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

    def _save_user_note(
        self, email: str, body: str, user_name: str | None = None
    ) -> None:
        notes_dir = self._data_dir / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        note_path = notes_dir / f"{self._email_to_filename(email)}.txt"
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"Date: {now}\n--------------------------------\n{body}\n"
        if not note_path.is_file() and user_name is not None:
            header = f"Email: {email}\nName: {user_name}\n"
            note_path.write_text(header + "\n" + entry)
        else:
            with note_path.open("a") as f:
                f.write("\n" + entry)

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
            SendRequest(
                to=[email],
                subject=template.subject,
                body=text_body,
                html_body=html_body,
                send_at="immediate",
            )
        )
        logger.info("Confirmation: queued for=%s action=%s", email, action)

    def handle_email(self, sender: str, subject: str, body: str) -> None:
        text = (subject + " " + body).lower()
        if "unsubscribe" in text:
            self.remove(sender)
            user_name = self._read_user_name(sender) or self._extract_user_name(
                subject, body, sender
            )
            self._save_user_note(sender, body, user_name)
            self._send_confirmation(sender, "unsubscribe", user_name)
            logger.info("Action: UNSUBSCRIBE sender=%s name=%s", sender, user_name)
        elif "subscribe" in text:
            self.add(sender)
            user_name = self._extract_user_name(subject, body, sender)
            self._save_user_note(sender, body, user_name)
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
