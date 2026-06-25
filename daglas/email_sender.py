import logging
import smtplib
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import daglas.config
from daglas.lesson.formatter import Email

logger = logging.getLogger(__name__)


@dataclass
class SendResult:
    success_count: int = 0
    failure_count: int = 0
    errors: list[str] = field(default_factory=list)


class SmtpSender:
    def __init__(
        self,
        host: str = "",
        port: int = 587,
        user: str = "",
        password: str = "",
        from_address: str = "",
    ):
        cfg = daglas.config.config
        self.host = host or (cfg.smtp_host if cfg else "")
        self.port = port if port != 587 else (cfg.smtp_port if cfg else 587)
        self.user = user or (cfg.smtp_user if cfg else "")
        self.password = password or (cfg.smtp_password if cfg else "")
        self.from_address = from_address or (cfg.from_address if cfg else "")

    @staticmethod
    def _build_message(email: Email, from_addr: str) -> str:
        msg = MIMEMultipart("alternative")
        msg["From"] = from_addr
        msg["To"] = "undisclosed-recipients: ;"
        msg["Subject"] = email.subject
        msg.attach(MIMEText(email.text_body, "plain", "utf-8"))
        msg.attach(MIMEText(email.html_body, "html", "utf-8"))
        return msg.as_string()

    @staticmethod
    def _chunks(items: list[str], size: int) -> list[list[str]]:
        return [items[i : i + size] for i in range(0, len(items), size)]

    def send(
        self,
        email: Email,
        recipients: list[str],
        *,
        batch_size: int = 0,
        dry_run: bool = False,
    ) -> SendResult:
        result = SendResult()
        if not recipients or not self.from_address:
            logger.warning("No recipients or from_address — skipping send")
            return result
        if dry_run:
            logger.info(
                "Dry-run: would send %r to %d recipient(s)",
                email.subject,
                len(recipients),
            )
            return result
        logger.info(
            "Sending %r to %d recipient(s) via %s:%d",
            email.subject,
            len(recipients),
            self.host,
            self.port,
        )
        try:
            conn = smtplib.SMTP(self.host, self.port, timeout=30)
            conn.starttls()
            logger.info("SMTP connected and TLS started")
            if self.user:
                conn.login(self.user, self.password)
                logger.info("SMTP authenticated as %s", self.user)
        except Exception as e:
            logger.error("SMTP connection failed: %s", e)
            result.failure_count = len(recipients)
            result.errors.append(f"SMTP connection failed: {e}")
            return result

        raw = self._build_message(email, self.from_address)
        batches = (
            self._chunks(recipients, batch_size) if batch_size > 0 else [recipients]
        )
        for batch in batches:
            try:
                failures = conn.sendmail(self.from_address, batch, raw)
                if failures:
                    successful = len(batch) - len(failures)
                    result.success_count += successful
                    for addr, err in failures.items():
                        logger.error("Failed to send to %s: %s", addr, err)
                        result.failure_count += 1
                        result.errors.append(f"Failed to send to {addr}: {err}")
                else:
                    result.success_count += len(batch)
            except smtplib.SMTPRecipientsRefused as e:
                logger.error("Batch entirely refused: %s", e)
                result.failure_count += len(batch)
                result.errors.append(f"Batch entirely refused: {e}")
            except Exception as e:
                logger.error("Batch send failed: %s", e)
                result.failure_count += len(batch)
                result.errors.append(f"Batch send failed: {e}")
        try:
            conn.quit()
            logger.info("SMTP disconnected")
        except Exception:
            pass
        logger.info(
            "Send complete: subject=%r ok=%d failed=%d",
            email.subject,
            result.success_count,
            result.failure_count,
        )
        return result
