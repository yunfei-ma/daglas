from pathlib import Path
from unittest.mock import MagicMock, patch


from daglas.lesson.generator import generate_lesson


class TestGenerateLesson:
    def test_dry_run_returns_none(self, tmp_path: Path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "system.md").write_text("System prompt")
        (prompts_dir / "user.md").write_text("test")

        with patch("daglas.config.config") as mock_config:
            mock_config.prompts_dir = str(prompts_dir)
            result = generate_lesson(MagicMock(), [], dry_run=True)
            assert result is None

    def test_generate_lesson_calls_provider(self, tmp_path: Path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "system.md").write_text("Vocab: {vocab_count}")
        (prompts_dir / "user.md").write_text(
            "Context: {context}\nLevel: {level}\nVocab: {vocab_count}"
        )

        with patch("daglas.config.config") as mock_config:
            mock_config.prompts_dir = str(prompts_dir)
            mock_config.lesson_level = "beginner"
            mock_config.vocab_count = 8

            provider = MagicMock()
            provider.prompt.return_value = "# Lesson\n\nHej!"

            articles = [{"title": "Test", "body": "En svensk artikel"}]
            result = generate_lesson(provider, articles)

            assert result == "# Lesson\n\nHej!"
            provider.prompt.assert_called_once()
            _args, kwargs = provider.prompt.call_args
            system_prompt = kwargs["system"]
            user_prompt = kwargs["user"]
            assert "Vocab: 8" in system_prompt
            assert "beginner" in user_prompt
            assert "8" in user_prompt

    def test_provider_none_returns_none(self, tmp_path: Path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "system.md").write_text("System prompt")
        (prompts_dir / "user.md").write_text("test")

        with patch("daglas.config.config") as mock_config:
            mock_config.prompts_dir = str(prompts_dir)
            provider = MagicMock()
            provider.prompt.return_value = None
            result = generate_lesson(provider, [])
            assert result is None
