from pathlib import Path
from unittest.mock import MagicMock, patch


from daglas.lesson.generator import _truncate_context, generate_lesson


class TestTruncateContext:
    def test_truncates_when_over_limit(self):
        text = "a" * 1000
        result = _truncate_context(text, 100)
        assert len(result) == 103
        assert result.endswith("...")

    def test_does_not_truncate_when_under_limit(self):
        text = "a" * 50
        result = _truncate_context(text, 100)
        assert result == text

    def test_handles_empty_string(self):
        assert _truncate_context("", 100) == ""


class TestGenerateLesson:
    def test_dry_run_returns_none(self, tmp_path: Path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "system.md").write_text("System prompt")
        (prompts_dir / "user.md").write_text("test")

        with patch("daglas.config.config") as mock_config:
            mock_config.prompts_dir = str(prompts_dir)
            mock_config.max_context_length = 500
            result = generate_lesson(MagicMock(), [], dry_run=True)
            assert result is None

    def test_generate_lesson_calls_provider(self, tmp_path: Path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "system.md").write_text("System prompt")
        (prompts_dir / "user.md").write_text(
            "Context: {context}\nLevel: {level}\nVocab: {vocab_count}"
        )

        with patch("daglas.config.config") as mock_config:
            mock_config.prompts_dir = str(prompts_dir)
            mock_config.max_context_length = 500
            mock_config.lesson_level = "beginner"
            mock_config.vocab_count = 8

            provider = MagicMock()
            provider.prompt.return_value = "# Lesson\n\nHej!"

            articles = [{"title": "Test", "body": "En svensk artikel"}]
            result = generate_lesson(provider, articles)

            assert result == "# Lesson\n\nHej!"
            provider.prompt.assert_called_once()
            _args, kwargs = provider.prompt.call_args
            user_prompt = kwargs["user"]
            assert "beginner" in user_prompt
            assert "8" in user_prompt
