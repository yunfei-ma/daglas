from dataclasses import dataclass


@dataclass
class Email:
    subject: str = ""
    html_body: str = ""
    text_body: str = ""


DEFAULT_SUBJECT = "Dagläsa — Daily Swedish Lesson"


def format_email(lesson_text: str) -> Email:
    if not lesson_text.strip():
        return Email(subject=DEFAULT_SUBJECT, html_body="", text_body="")

    lines = lesson_text.strip().split("\n")
    subject = lines[0] if lines else DEFAULT_SUBJECT
    if subject.startswith("#"):
        subject = subject.lstrip("#").strip()

    html_parts = [_html_header()]
    for line in lines:
        html_parts.append(_line_to_html(line))

    html_parts.append("</div></body></html>")

    return Email(
        subject=subject,
        html_body="\n".join(html_parts),
        text_body=lesson_text.strip(),
    )


def _html_header() -> str:
    return """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px;">
<div style="background:#f9f9f9;border-radius:8px;padding:24px;">"""


def _line_to_html(line: str) -> str:
    line = line.strip()
    if not line:
        return "<br>"
    if line.startswith("## "):
        return f"<h3 style='color:#2c3e50;margin-top:24px;'>{line[3:]}</h3>"
    if line.startswith("# "):
        return f"<h2 style='color:#2c3e50;'>{line[2:]}</h2>"
    if line.startswith("- "):
        return f"<li>{line[2:]}</li>"
    if line.startswith("**") and line.endswith("**"):
        return f"<p><strong>{line[2:-2]}</strong></p>"
    return f"<p>{line}</p>"
