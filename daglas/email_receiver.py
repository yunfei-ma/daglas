from __future__ import annotations

import email
import imaplib
import logging
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
        logger.debug("EmailReceiver imap_host=%s", self._imap_host)

    def _connect(self):
        conn = imaplib.IMAP4_SSL(self._imap_host, self._imap_port, timeout=30)
        conn.sock.settimeout(30)
        conn.login(self._imap_user, self._imap_password)
        conn.select("INBOX")
        return conn

    @staticmethod
    def _get_body(msg) -> str:
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

    def _scan_folder(self, conn, folder: str) -> None:
        conn.select(folder)
        _, data = conn.search(None, "UNSEEN")
        msg_ids = data[0].split() if data[0] else []
        for msg_id in msg_ids:
            try:
                if self._parse_and_push(conn, msg_id):
                    logger.info("Scanned folder=%s", folder)
            except Exception as e:
                logger.error("Failed to process message %s: %s", msg_id, e)

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

    def poll(self) -> None:
        try:
            conn = self._connect()
        except Exception as e:
            logger.error("IMAP connection failed: %s", e)
            return
        try:
            self._scan_folder(conn, "INBOX")
            spam_folder = self._find_spam_folder(conn)
            if spam_folder:
                self._scan_folder(conn, spam_folder)
        except Exception as e:
            logger.error("IMAP search failed: %s", e)
        finally:
            try:
                conn.logout()
            except Exception:
                pass
