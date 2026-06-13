import email
import imaplib
import logging
import time
from email.utils import parseaddr

import daglas.config
from daglas.email_queue import RawEmail

logger = logging.getLogger(__name__)


class EmailReceiver:
    def __init__(
        self,
        queue,
        *,
        imap_host: str = "",
        imap_port: int = 993,
        imap_user: str = "",
        imap_password: str = "",
        poll_interval: int = 300,
    ):
        self._queue = queue
        cfg = daglas.config.config

        self._imap_host = imap_host or (cfg.imap_host if cfg else "")
        self._imap_port = imap_port or (cfg.imap_port if cfg else 993)
        self._imap_user = imap_user or (cfg.imap_user if cfg else "")
        self._imap_password = imap_password or (cfg.imap_password if cfg else "")
        self._poll_interval = (
            poll_interval
            if poll_interval != 300
            else (cfg.email_receiver_poll_interval if cfg else 300)
        )

    def _connect(self):
        conn = imaplib.IMAP4_SSL(self._imap_host, self._imap_port)
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
        raw_email = RawEmail(
            sender=sender, subject=subject, body=body, raw_bytes=raw_bytes
        )
        self._queue.push("incoming", raw_email)
        conn.store(msg_id, "+FLAGS", "\\Seen")
        return True

    def check_once(self) -> int:
        count = 0
        try:
            conn = self._connect()
        except Exception as e:
            logger.error("IMAP connection failed: %s", e)
            return 0
        try:
            _, data = conn.search(None, "UNSEEN")
            msg_ids = data[0].split() if data[0] else []
            for msg_id in msg_ids:
                try:
                    if self._parse_and_push(conn, msg_id):
                        count += 1
                except Exception as e:
                    logger.error("Failed to process message %s: %s", msg_id, e)
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
        while max_iterations is None or iterations < max_iterations:
            start = time.monotonic()
            count = self.check_once()
            if count:
                logger.info("Pushed %d email(s) to queue", count)
            elapsed = time.monotonic() - start
            iterations += 1
            if max_iterations is None or iterations < max_iterations:
                sleep_time = max(0, self._poll_interval - elapsed)
                time.sleep(sleep_time)
