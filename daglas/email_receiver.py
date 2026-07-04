from __future__ import annotations

import email
import imaplib
import logging
import threading
import time
from email.utils import parseaddr

import daglas.config
from daglas.email_queue import RawEmail

logger = logging.getLogger(__name__)


class EmailReceiver:
    def __init__(self, queue):
        self._queue = queue
        cfg = daglas.config.config

        self._imap_host = cfg.imap_host if cfg else ""
        self._imap_port = cfg.imap_port if cfg else 993
        self._imap_user = cfg.imap_user if cfg else ""
        self._imap_password = cfg.imap_password if cfg else ""
        self._poll_interval = cfg.email_receiver_poll_interval if cfg else 300
        logger.debug(
            "EmailReceiver poll_interval=%ds imap_host=%s",
            self._poll_interval,
            self._imap_host,
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            logger.warning("EmailReceiver is already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("EmailReceiver started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("EmailReceiver stopped")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                start = time.monotonic()
                count = self.check_once()
                if count:
                    logger.info("Pushed %d email(s) to queue", count)
                elapsed = time.monotonic() - start
                if not self._stop_event.is_set():
                    sleep_time = max(0, self._poll_interval - elapsed)
                    logger.debug(
                        "EmailReceiver cycle: check_took=%.2fs "
                        "poll_interval=%ds next_check_in=%.2fs",
                        elapsed,
                        self._poll_interval,
                        sleep_time,
                    )
                    self._stop_event.wait(timeout=sleep_time)
            except Exception:
                logger.exception("EmailReceiver._run: unhandled exception, restarting")

    def _connect(self):
        conn = imaplib.IMAP4_SSL(self._imap_host, self._imap_port, timeout=30)
        conn.sock.settimeout(30)
        conn.login(self._imap_user, self._imap_password)
        conn.select("INBOX")
        return conn

    def _get_body(self, msg) -> str:
        parts: list[str] = []
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        parts.append(payload.decode("utf-8", errors="replace"))
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                parts.append(payload.decode("utf-8", errors="replace"))
        return "\n".join(parts)

    def _parse_and_push(self, conn, msg_id: bytes) -> bool:
        _, data = conn.fetch(msg_id, "(RFC822)")
        raw_bytes = data[0][1]
        msg = email.message_from_bytes(raw_bytes)
        _, sender = parseaddr(msg["From"])
        if not sender:
            return False
        subject = msg["Subject"] or ""
        body = self._get_body(msg)
        logger.info("Received: from=%s subject=%s", sender, subject)
        raw_email = RawEmail(
            sender=sender, subject=subject, body=body, raw_bytes=raw_bytes
        )
        self._queue.push("incoming", raw_email)
        conn.store(msg_id, "+FLAGS", "\\Seen")
        return True

    @staticmethod
    def _spam_folder_names() -> list[str]:
        return ["[Gmail]/Spam", "[Gmail]/Skr\u00e4ppost", "[Gmail]/Bulk Mail", "Spam"]

    def _scan_folder(self, conn, folder: str) -> int:
        conn.select(folder)
        _, data = conn.search(None, "UNSEEN")
        msg_ids = data[0].split() if data[0] else []
        count = 0
        for msg_id in msg_ids:
            try:
                if self._parse_and_push(conn, msg_id):
                    count += 1
            except Exception as e:
                logger.error("Failed to process message %s: %s", msg_id, e)
        if count:
            logger.info("Scanned folder=%s unseen=%d", folder, count)
        return count

    def _find_spam_folder(self, conn) -> str | None:
        try:
            typ, folders = conn.list()
        except Exception:
            return None
        candidates = self._spam_folder_names()
        for line in folders:
            decoded = line.decode(errors="replace")
            for candidate in candidates:
                if candidate.lower() in decoded.lower():
                    return candidate
        return None

    def check_once(self) -> int:
        logger.info("Checking for new email...")
        count = 0
        try:
            conn = self._connect()
        except Exception as e:
            logger.error("IMAP connection failed: %s", e)
            return 0
        try:
            count += self._scan_folder(conn, "INBOX")
            spam_folder = self._find_spam_folder(conn)
            if spam_folder:
                count += self._scan_folder(conn, spam_folder)
        except Exception as e:
            logger.error("IMAP search failed: %s", e)
        finally:
            try:
                conn.logout()
            except Exception:
                pass
        return count

    def run_loop(self, max_iterations: int | None = None) -> None:
        iterations = 0
        while (
            max_iterations is None or iterations < max_iterations
        ) and not self._stop_event.is_set():
            start = time.monotonic()
            count = self.check_once()
            if count:
                logger.info("Pushed %d email(s) to queue", count)
            elapsed = time.monotonic() - start
            iterations += 1
            logger.debug(
                "EmailReceiver cycle %d: check_took=%.2fs poll_interval=%ds",
                iterations,
                elapsed,
                self._poll_interval,
            )
            if (
                max_iterations is None or iterations < max_iterations
            ) and not self._stop_event.is_set():
                sleep_time = max(0, self._poll_interval - elapsed)
                time.sleep(sleep_time)
