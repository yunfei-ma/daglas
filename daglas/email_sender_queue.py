from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import daglas.config
from daglas.email_sender import SmtpSender
from daglas.lesson.formatter import Email

logger = logging.getLogger(__name__)


@dataclass
class SendRequest:
    to: list[str] = field(default_factory=list)
    subject: str = ""
    body: str = ""
    html_body: str = ""
    send_at: str | None = None
    queued_at: str = ""


class EmailSenderQueue:
    def __init__(self):
        cfg = daglas.config.config
        self._sender = SmtpSender()
        data_dir = Path(cfg.data_dir) if cfg else Path("data")
        self._immediate_path = data_dir / "email_sender_queue" / "immediate.jsonl"
        self._scheduled_path = data_dir / "email_sender_queue" / "scheduled.jsonl"
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._immediate_thread: threading.Thread | None = None
        self._scheduled_thread: threading.Thread | None = None
        self._immediate_interval = (
            cfg.email_sender_queue_immediate_interval if cfg else 20
        )
        self._scheduled_interval = (
            cfg.email_sender_queue_scheduled_interval if cfg else 300
        )

    def push(self, request: SendRequest) -> None:
        if not request.queued_at:
            request.queued_at = datetime.now(timezone.utc).isoformat()
        if request.send_at is None:
            logger.error(
                "SendRequest for %s has send_at=None — not queued",
                request.subject,
            )
            return
        if request.send_at == "immediate":
            self._write_to_file(self._immediate_path, request)
        elif self._is_iso_datetime(request.send_at):
            self._write_to_file(self._scheduled_path, request)
        else:
            logger.error(
                "SendRequest for %s has invalid send_at=%r — not queued",
                request.subject,
                request.send_at,
            )

    @staticmethod
    def _is_iso_datetime(value: str) -> bool:
        try:
            datetime.fromisoformat(value)
            return True
        except (ValueError, TypeError):
            return False

    def _write_to_file(self, path: Path, request: SendRequest) -> None:
        data = {
            "to": request.to,
            "subject": request.subject,
            "body": request.body,
            "html_body": request.html_body,
            "send_at": request.send_at,
            "queued_at": request.queued_at,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(data, ensure_ascii=False)
        with self._lock:
            with open(path, "a") as f:
                f.write(line + "\n")
        logger.info(
            "Queued: send_at=%s subject=%s recipients=%d",
            request.send_at,
            request.subject,
            len(request.to),
        )

    @staticmethod
    def _deserialize(line: str) -> SendRequest:
        data = json.loads(line)
        return SendRequest(
            to=data.get("to", []),
            subject=data.get("subject", ""),
            body=data.get("body", ""),
            html_body=data.get("html_body", ""),
            send_at=data.get("send_at"),
            queued_at=data.get("queued_at", ""),
        )

    def _read_all(self, path: Path) -> list[str]:
        with self._lock:
            if not path.is_file():
                return []
            lines = path.read_text().splitlines()
            path.unlink()
        return lines

    def _write_all(self, path: Path, lines: list[str]) -> None:
        if not lines:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            path.write_text("\n".join(lines) + "\n")

    def dispatch(self, request: SendRequest) -> None:
        email = Email(
            subject=request.subject,
            text_body=request.body,
            html_body=request.html_body or request.body,
        )
        result = self._sender.send(email, request.to)
        logger.info(
            "Sent: subject=%s recipients=%d ok=%d failed=%d",
            request.subject,
            len(request.to),
            result.success_count,
            result.failure_count,
        )
        for err in result.errors:
            logger.error("Send error: %s", err)

    def _immediate_loop(self) -> None:
        while not self._stop_event.is_set():
            lines = self._read_all(self._immediate_path)
            for line in lines:
                request = self._deserialize(line)
                self.dispatch(request)
            self._stop_event.wait(timeout=self._immediate_interval)

    def _scheduled_loop(self) -> None:
        while not self._stop_event.is_set():
            lines = self._read_all(self._scheduled_path)
            pending: list[str] = []
            now = datetime.now(timezone.utc)
            for line in lines:
                request = self._deserialize(line)
                try:
                    send_at = datetime.fromisoformat(request.send_at)
                    if send_at.tzinfo is None:
                        send_at = send_at.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    logger.error("Invalid send_at=%r — skipping", request.send_at)
                    continue
                if send_at <= now:
                    self.dispatch(request)
                else:
                    pending.append(line)
            self._write_all(self._scheduled_path, pending)
            self._stop_event.wait(timeout=self._scheduled_interval)

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
        if self._immediate_thread and self._immediate_thread.is_alive():
            self._immediate_thread.join(timeout=5)
        if self._scheduled_thread and self._scheduled_thread.is_alive():
            self._scheduled_thread.join(timeout=5)
        logger.info("EmailSenderQueue stopped")
