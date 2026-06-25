from unittest.mock import MagicMock, patch

import smtplib

from daglas.email_sender import SmtpSender
from daglas.lesson.formatter import Email


def _make_email() -> Email:
    return Email(
        subject="Dagl\u00e4sa \u2014 Today's Lesson",
        text_body="Hej! This is a lesson.",
        html_body="<p>Hej! This is a lesson.</p>",
    )


class TestSmtpSender:
    def test_send_to_one(self):
        sender = SmtpSender(
            host="smtp.test.com", user="u", password="p", from_address="a@b.com"
        )
        mock_smtp = MagicMock()
        mock_smtp.sendmail.return_value = {}
        with patch("smtplib.SMTP", return_value=mock_smtp):
            result = sender.send(_make_email(), ["alice@example.com"])
        assert result.success_count == 1
        assert result.failure_count == 0

    def test_send_to_multiple_bcc(self):
        sender = SmtpSender(
            host="smtp.test.com", user="u", password="p", from_address="a@b.com"
        )
        mock_smtp = MagicMock()
        mock_smtp.sendmail.return_value = {}
        with patch("smtplib.SMTP", return_value=mock_smtp):
            result = sender.send(_make_email(), ["a@x.com", "b@x.com", "c@x.com"])
        assert result.success_count == 3
        assert result.failure_count == 0
        assert mock_smtp.sendmail.call_count == 1

    def test_batch_split(self):
        sender = SmtpSender(
            host="smtp.test.com", user="u", password="p", from_address="a@b.com"
        )
        mock_smtp = MagicMock()
        mock_smtp.sendmail.return_value = {}
        recipients = [f"u{i}@x.com" for i in range(5)]
        with patch("smtplib.SMTP", return_value=mock_smtp):
            result = sender.send(_make_email(), recipients, batch_size=2)
        assert result.success_count == 5
        assert result.failure_count == 0
        assert mock_smtp.sendmail.call_count == 3

    def test_batch_size_zero(self):
        sender = SmtpSender(
            host="smtp.test.com", user="u", password="p", from_address="a@b.com"
        )
        mock_smtp = MagicMock()
        mock_smtp.sendmail.return_value = {}
        recipients = [f"u{i}@x.com" for i in range(5)]
        with patch("smtplib.SMTP", return_value=mock_smtp):
            result = sender.send(_make_email(), recipients, batch_size=0)
        assert result.success_count == 5
        assert mock_smtp.sendmail.call_count == 1

    def test_partial_failure_batch(self):
        sender = SmtpSender(
            host="smtp.test.com", user="u", password="p", from_address="a@b.com"
        )
        mock_smtp = MagicMock()
        mock_smtp.sendmail.return_value = {"b@x.com": (550, "Mailbox not found")}
        with patch("smtplib.SMTP", return_value=mock_smtp):
            result = sender.send(_make_email(), ["a@x.com", "b@x.com", "c@x.com"])
        assert result.success_count == 2
        assert result.failure_count == 1
        assert "Mailbox not found" in result.errors[0]

    def test_batch_entirely_refused(self):
        sender = SmtpSender(
            host="smtp.test.com", user="u", password="p", from_address="a@b.com"
        )
        mock_smtp = MagicMock()
        mock_smtp.sendmail.side_effect = smtplib.SMTPRecipientsRefused(
            {addr: (550, "Refused") for addr in ["a@x.com", "b@x.com"]}
        )
        with patch("smtplib.SMTP", return_value=mock_smtp):
            result = sender.send(_make_email(), ["a@x.com", "b@x.com"])
        assert result.success_count == 0
        assert result.failure_count == 2

    def test_no_recipients(self):
        sender = SmtpSender(from_address="a@b.com")
        result = sender.send(_make_email(), [])
        assert result.success_count == 0
        assert result.failure_count == 0

    def test_dry_run(self):
        sender = SmtpSender(host="smtp.test.com", from_address="a@b.com")
        with patch("smtplib.SMTP") as mock_smtp:
            result = sender.send(_make_email(), ["a@x.com"], dry_run=True)
        assert result.success_count == 0
        mock_smtp.assert_not_called()

    def test_no_from_address(self):
        sender = SmtpSender(host="smtp.test.com")
        result = sender.send(_make_email(), ["a@x.com"])
        assert result.success_count == 0
        assert result.failure_count == 0

    def test_message_structure(self):
        sender = SmtpSender(from_address="teacher@daglas.com")
        raw = sender._build_message(_make_email(), "teacher@daglas.com")
        assert "From: teacher@daglas.com" in raw
        assert "To: undisclosed-recipients: ;" in raw
        assert "Subject:" in raw
        assert "Content-Type: multipart/alternative" in raw
        assert "text/plain" in raw
        assert "text/html" in raw

    def test_smtp_connection_failure(self):
        sender = SmtpSender(
            host="smtp.bad.com", user="u", password="p", from_address="a@b.com"
        )

        def broken_smtp(*args, **kwargs):
            raise ConnectionError("DNS failure")

        with patch("smtplib.SMTP", broken_smtp):
            result = sender.send(_make_email(), ["a@x.com", "b@x.com"])
        assert result.success_count == 0
        assert result.failure_count == 2
