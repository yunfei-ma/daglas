from pathlib import Path

from daglas.config import DaglasConfig, load_config


class TestUserConfig:
    def test_loads_user_config_directly(self, tmp_path: Path):
        user = tmp_path / "config.yaml"
        user.write_text("max_context_length: 1000\nvocab_count: 12\n")
        cfg = load_config(user)
        assert cfg.max_context_length == 1000
        assert cfg.vocab_count == 12
        assert cfg.lesson_level == "beginner"

    def test_empty_user_config_falls_back(self, tmp_path: Path):
        user = tmp_path / "config.yaml"
        user.write_text("")
        cfg = load_config(user)
        assert cfg.max_context_length == 500

    def test_comments_only_user_config_falls_back(self, tmp_path: Path):
        user = tmp_path / "config.yaml"
        user.write_text("# just comments\n")
        cfg = load_config(user)
        assert isinstance(cfg, DaglasConfig)
        assert cfg.max_context_length == 500

    def test_explicit_path(self, tmp_path: Path):
        user = tmp_path / "custom" / "settings.yaml"
        user.parent.mkdir(parents=True)
        user.write_text("vocab_count: 15\n")
        cfg = load_config(user)
        assert cfg.vocab_count == 15
        assert cfg.max_context_length == 500


class TestFirstRun:
    def test_creates_user_config_from_default(self, tmp_path: Path):
        default = tmp_path / "config_default.yaml"
        default.write_text("max_context_length: 500\nvocab_count: 8\n")
        user = tmp_path / "config.yaml"
        cfg = load_config(user, default_path=default)
        assert user.is_file()
        assert user.read_text() == default.read_text()
        assert cfg.max_context_length == 500
        assert cfg.vocab_count == 8

    def test_creates_nested_dir_when_missing(self, tmp_path: Path):
        default = tmp_path / "config_default.yaml"
        default.write_text("max_context_length: 500\n")
        user = tmp_path / "sub" / "config.yaml"
        cfg = load_config(user, default_path=default)
        assert user.is_file()
        assert cfg.max_context_length == 500

    def test_no_default_falls_back_to_hardcoded(self, tmp_path: Path):
        user = tmp_path / "config.yaml"
        cfg = load_config(user, default_path=tmp_path / "nonexistent.yaml")
        assert cfg.max_context_length == 500
        assert not user.exists()
