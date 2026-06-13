import smtplib
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import daglas.config
from daglas.lesson.formatter import Email


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
    def _build_message(email: Email, from_addr: str, to_addr: str) -> str:
        msg = MIMEMultipart("alternative")
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg["Subject"] = email.subject
        msg.attach(MIMEText(email.text_body, "plain", "utf-8"))
        msg.attach(MIMEText(email.html_body, "html", "utf-8"))
        return msg.as_string()

    def send(
        self,
        email: Email,
        recipients: list[str],
        *,
        dry_run: bool = False,
    ) -> SendResult:
        result = SendResult()
        if not recipients or not self.from_address:
            return result
        if dry_run:
            print(
                f"[dry-run] Would send '{email.subject}' to {len(recipients)} recipient(s)"
            )
            return result
        try:
            conn = smtplib.SMTP(self.host, self.port, timeout=30)
            conn.starttls()
            if self.user:
                conn.login(self.user, self.password)
        except Exception as e:
            result.failure_count = len(recipients)
            result.errors.append(f"SMTP connection failed: {e}")
            return result
        for recipient in recipients:
            try:
                raw = self._build_message(email, self.from_address, recipient)
                conn.sendmail(self.from_address, [recipient], raw)
                result.success_count += 1
            except Exception as e:
                result.failure_count += 1
                result.errors.append(f"Failed to send to {recipient}: {e}")
        try:
            conn.quit()
        except Exception:
            pass
        return result
