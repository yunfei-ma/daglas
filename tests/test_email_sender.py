from unittest.mock import MagicMock, patch

from daglas.email_sender import SmtpSender
from daglas.lesson.formatter import Email


def _make_email() -> Email:
    return Email(
        subject="Dagläsa — Today's Lesson",
        text_body="Hej! This is a lesson.",
        html_body="<p>Hej! This is a lesson.</p>",
    )


class TestSmtpSender:
    def test_send_to_one(self):
        sender = SmtpSender(
            host="smtp.test.com", user="u", password="p", from_address="a@b.com"
        )
        mock_smtp = MagicMock()
        with patch("smtplib.SMTP", return_value=mock_smtp):
            result = sender.send(_make_email(), ["alice@example.com"])
        assert result.success_count == 1
        assert result.failure_count == 0

    def test_send_to_multiple(self):
        sender = SmtpSender(
            host="smtp.test.com", user="u", password="p", from_address="a@b.com"
        )
        mock_smtp = MagicMock()
        with patch("smtplib.SMTP", return_value=mock_smtp):
            result = sender.send(_make_email(), ["a@x.com", "b@x.com", "c@x.com"])
        assert result.success_count == 3
        assert result.failure_count == 0
        assert mock_smtp.sendmail.call_count == 3

    def test_partial_failure(self):
        sender = SmtpSender(
            host="smtp.test.com", user="u", password="p", from_address="a@b.com"
        )
        mock_smtp = MagicMock()
        mock_smtp.sendmail.side_effect = [None, RuntimeError("Bounce")]

        with patch("smtplib.SMTP", return_value=mock_smtp):
            result = sender.send(_make_email(), ["a@x.com", "b@x.com"])
        assert result.success_count == 1
        assert result.failure_count == 1
        assert "Bounce" in result.errors[0]

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
        raw = sender._build_message(
            _make_email(), "teacher@daglas.com", "alice@example.com"
        )
        assert "From: teacher@daglas.com" in raw
        assert "To: alice@example.com" in raw
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
