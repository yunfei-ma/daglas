from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from uuid import UUID

import daglas.config

logger = logging.getLogger(__name__)


class UserNoteStore:
    def __init__(self, data_dir: str | Path | None = None):
        if data_dir is not None:
            self._notes_dir = Path(data_dir) / "notes"
        elif daglas.config.config is not None:
            self._notes_dir = Path(daglas.config.config.data_dir) / "notes"
        else:
            self._notes_dir = Path("data") / "notes"

    @staticmethod
    def email_to_filename(email: str) -> str:
        result = email.replace("@", "_").replace(".", "_")
        unsafe = '/\\:*?"<>|'
        for ch in unsafe:
            result = result.replace(ch, "_")
        return result

    def _note_path(self, email: str) -> Path:
        return self._notes_dir / f"{self.email_to_filename(email)}.txt"

    def _prepend_entry(self, path: Path, entry: str) -> None:
        if not path.is_file():
            path.write_text(entry)
            return
        existing = path.read_text()
        if existing.startswith("Email:"):
            idx = existing.find("\n\n")
            if idx != -1:
                header = existing[:idx]
                rest = existing[idx:].lstrip("\n")
                path.write_text(header + "\n\n" + entry.rstrip("\n") + "\n" + rest)
            else:
                path.write_text(entry.rstrip("\n") + "\n\n" + existing)
        else:
            path.write_text(entry.rstrip("\n") + "\n" + existing)

    def save_received(
        self, email: str, body: str, user_name: str | None = None
    ) -> None:
        path = self._note_path(email)
        self._notes_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = (
            f"Date: {now}\nType: received\n--------------------------------\n{body}\n"
        )
        if not path.is_file() and user_name is not None:
            header = f"Email: {email}\nName: {user_name}\n"
            path.write_text(header + "\n" + entry)
        else:
            self._prepend_entry(path, entry)

    def save_sent(
        self,
        to: Sequence[str],
        subject: str,
        text_body: str,
        html_body: str,
        send_at: str | None,
        request_id: UUID,
    ) -> None:
        self._notes_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [
            f"Date: {now}",
            "Type: sent",
            f"Subject: {subject}",
            f"Send At: {send_at}" if send_at is not None else "Send At: ",
            f"Request ID: {request_id}",
            "--------------------------------",
            text_body,
        ]
        entry = "\n".join(lines) + "\n"
        for recipient in to:
            self._prepend_entry(self._note_path(recipient), entry)

    def read_user_name(self, email: str) -> str | None:
        path = self._note_path(email)
        if not path.is_file():
            return None
        for line in path.read_text().splitlines():
            if line.startswith("Name: "):
                return line[6:]
        return None
