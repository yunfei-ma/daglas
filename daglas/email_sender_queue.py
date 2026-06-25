from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import daglas.config
from daglas.email_sender import SendResult, SmtpSender
from daglas.lesson.formatter import Email
from daglas.user_note_store import UserNoteStore

logger = logging.getLogger(__name__)


@dataclass
class MailItem:
    to: list[str]
    subject: str
    text_body: str
    request_id: UUID = field(default_factory=uuid4)
    html_body: str = ""
    send_at: str | None = None


class EmailSenderQueue:
    def __init__(self):
        cfg = daglas.config.config
        self._sender = SmtpSender()
        data_dir = Path(cfg.data_dir) if cfg else Path("data")
        self._notes = UserNoteStore(data_dir)
        self._immediate_path = data_dir / "email_sender_queue" / "immediate.jsonl"
        self._scheduled_path = data_dir / "email_sender_queue" / "scheduled.jsonl"
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._immediate_notify = threading.Event()
        self._immediate_thread: threading.Thread | None = None
        self._scheduled_thread: threading.Thread | None = None

        if cfg:
            self._immediate_success_backoff = cfg.email_sender_immediate_success_backoff
            self._immediate_empty_interval = cfg.email_sender_immediate_empty_interval
            self._scheduled_success_backoff = cfg.email_sender_scheduled_success_backoff
            self._scheduled_empty_interval = cfg.email_sender_scheduled_empty_interval
        else:
            self._immediate_success_backoff = 5
            self._immediate_empty_interval = 20
            self._scheduled_success_backoff = 5
            self._scheduled_empty_interval = 60

    # --- Serialization ---

    @staticmethod
    def _serialize(item: MailItem) -> str:
        data = {
            "request_id": str(item.request_id),
            "to": item.to,
            "subject": item.subject,
            "text_body": item.text_body,
            "html_body": item.html_body,
            "send_at": item.send_at,
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }
        return json.dumps(data, ensure_ascii=False)

    @staticmethod
    def _deserialize(line: str) -> MailItem:
        data = json.loads(line)
        rid = data.get("request_id")
        return MailItem(
            request_id=UUID(rid) if rid else uuid4(),
            to=data.get("to", []),
            subject=data.get("subject", ""),
            text_body=data.get("text_body", ""),
            html_body=data.get("html_body", ""),
            send_at=data.get("send_at"),
        )

    # --- Immediate queue ---

    def _pop(self) -> MailItem | None:
        with self._lock:
            if not self._immediate_path.is_file():
                return None
            lines = self._immediate_path.read_text().splitlines()
            if not lines:
                return None
            first, *rest = lines
            if rest:
                self._immediate_path.write_text("\n".join(rest) + "\n")
            else:
                self._immediate_path.unlink(missing_ok=True)
        return self._deserialize(first)

    def _immediate_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self._pop()
                if item is not None:
                    result = self._send(item)
                    if result is not None and result.success_count > 0:
                        self._notes.save_sent(
                            to=item.to,
                            subject=item.subject,
                            text_body=item.text_body,
                            html_body=item.html_body,
                            send_at=item.send_at,
                            request_id=item.request_id,
                        )
                    self._stop_event.wait(timeout=self._immediate_success_backoff)
                else:
                    self._immediate_notify.clear()
                    self._immediate_notify.wait(timeout=self._immediate_empty_interval)
            except Exception:
                logger.exception("Immediate poll thread error")

    # --- Scheduled queue ---

    def get_next_due(self, now: datetime) -> MailItem | None:
        cutoff = now + timedelta(minutes=30)
        with self._lock:
            if not self._scheduled_path.is_file():
                return None
            for line in self._scheduled_path.read_text().splitlines():
                try:
                    data = json.loads(line)
                    send_at_str = data.get("send_at")
                    if not send_at_str:
                        continue
                    send_at = datetime.fromisoformat(send_at_str)
                    if send_at.tzinfo is None:
                        send_at = send_at.replace(tzinfo=timezone.utc)
                    if send_at <= cutoff:
                        rid = data.get("request_id")
                        return MailItem(
                            request_id=UUID(rid) if rid else uuid4(),
                            to=data.get("to", []),
                            subject=data.get("subject", ""),
                            text_body=data.get("text_body", ""),
                            html_body=data.get("html_body", ""),
                            send_at=send_at_str,
                        )
                except (ValueError, TypeError):
                    continue
        return None

    def delete(self, request_id: UUID) -> None:
        rid_str = str(request_id)
        with self._lock:
            if not self._scheduled_path.is_file():
                return
            lines = self._scheduled_path.read_text().splitlines()
            remaining = [
                line for line in lines if json.loads(line).get("request_id") != rid_str
            ]
            if remaining:
                self._scheduled_path.write_text("\n".join(remaining) + "\n")
            else:
                self._scheduled_path.unlink(missing_ok=True)

    def _scheduled_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                now = datetime.now(timezone.utc)
                item = self.get_next_due(now)
                if item is not None:
                    self._send(item)
                    self.delete(item.request_id)
                    self._stop_event.wait(timeout=self._scheduled_success_backoff)
                else:
                    self._stop_event.wait(timeout=self._scheduled_empty_interval)
            except Exception:
                logger.exception("Scheduled poll thread error")

    # --- Push ---

    @staticmethod
    def _is_iso_datetime(value: str) -> bool:
        try:
            datetime.fromisoformat(value)
            return True
        except (ValueError, TypeError):
            return False

    def _insert_sorted(self, path: Path, item: MailItem) -> None:
        new_line = self._serialize(item)
        new_send_at = datetime.fromisoformat(item.send_at)
        if new_send_at.tzinfo is None:
            new_send_at = new_send_at.replace(tzinfo=timezone.utc)
        with self._lock:
            lines: list[str] = []
            if path.is_file():
                lines = path.read_text().splitlines()
            insert_idx = len(lines)
            for i, line in enumerate(lines):
                try:
                    data = json.loads(line)
                    existing_send_at = datetime.fromisoformat(data.get("send_at", ""))
                    if existing_send_at.tzinfo is None:
                        existing_send_at = existing_send_at.replace(tzinfo=timezone.utc)
                    if existing_send_at <= new_send_at:
                        insert_idx = i + 1
                    else:
                        insert_idx = i
                        break
                except (ValueError, TypeError):
                    continue
            lines.insert(insert_idx, new_line)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines) + "\n")

    def _append_to_file(self, path: Path, item: MailItem) -> None:
        line = self._serialize(item)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with open(path, "a") as f:
                f.write(line + "\n")

    def push(self, request: MailItem) -> None:
        if request.send_at is None:
            logger.error(
                "MailItem for %s has send_at=None — not queued", request.subject
            )
            return
        if request.send_at == "immediate":
            self._append_to_file(self._immediate_path, request)
            self._immediate_notify.set()
        elif self._is_iso_datetime(request.send_at):
            self._insert_sorted(self._scheduled_path, request)
        else:
            logger.error(
                "MailItem for %s has invalid send_at=%r — not queued",
                request.subject,
                request.send_at,
            )

    # --- Send ---

    def _send(self, item: MailItem) -> SendResult | None:
        email = Email(
            subject=item.subject,
            text_body=item.text_body,
            html_body=item.html_body or item.text_body,
        )
        try:
            result = self._sender.send(email, item.to)
            logger.info(
                "Sent: subject=%s recipients=%d ok=%d failed=%d",
                item.subject,
                len(item.to),
                result.success_count,
                result.failure_count,
            )
            for err in result.errors:
                logger.error("Send error: %s", err)
            return result
        except Exception:
            logger.exception("Failed to send: subject=%s", item.subject)
            return None

    # --- Lifecycle ---

    def start(self) -> None:
        self._stop_event.clear()
        self._immediate_thread = threading.Thread(
            target=self._immediate_loop, daemon=True
        )
        self._scheduled_thread = threading.Thread(
            target=self._scheduled_loop, daemon=True
        )
        self._immediate_thread.start()
        self._scheduled_thread.start()
        logger.info("EmailSenderQueue started")

    def stop(self) -> None:
        self._stop_event.set()
        self._immediate_notify.set()
        if self._immediate_thread and self._immediate_thread.is_alive():
            self._immediate_thread.join(timeout=5)
        if self._scheduled_thread and self._scheduled_thread.is_alive():
            self._scheduled_thread.join(timeout=5)
        logger.info("EmailSenderQueue stopped")
