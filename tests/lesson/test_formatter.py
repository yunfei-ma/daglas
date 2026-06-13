from daglas.lesson.formatter import format_email


class TestFormatEmail:
    def test_format_basic_lesson(self):
        lesson = "# Swedish Lesson\n\n## Vocabulary\n\n- hej = hello\n\nHej!"
        email = format_email(lesson)
        assert email.subject == "Swedish Lesson"
        assert "hej" in email.html_body.lower()
        assert "hej" in email.text_body

    def test_subject_from_first_line(self):
        lesson = "Daily Swedish"
        email = format_email(lesson)
        assert email.subject == "Daily Swedish"

    def test_html_structure(self):
        lesson = "# Lesson"
        email = format_email(lesson)
        assert email.html_body.startswith("<!DOCTYPE html>")
        assert "</html>" in email.html_body
        assert email.text_body == "# Lesson"

    def test_empty_lesson(self):
        email = format_email("")
        assert email.subject == "Dagläsa — Daily Swedish Lesson"
        assert email.text_body == ""
