from pathlib import Path

from daglas.config import DaglasConfig, load_config


class TestUserConfig:
    def test_loads_user_config_directly(self, tmp_path: Path):
        user = tmp_path / "config.yaml"
        user.write_text("vocab_count: 12\n")
        cfg = load_config(user)
        assert cfg.vocab_count == 12
        assert cfg.lesson_level == "beginner"

    def test_empty_user_config_falls_back(self, tmp_path: Path):
        user = tmp_path / "config.yaml"
        user.write_text("")
        cfg = load_config(user)
        assert cfg.article_word_limit == 100

    def test_comments_only_user_config_falls_back(self, tmp_path: Path):
        user = tmp_path / "config.yaml"
        user.write_text("# just comments\n")
        cfg = load_config(user)
        assert isinstance(cfg, DaglasConfig)
        assert cfg.article_word_limit == 100

    def test_explicit_path(self, tmp_path: Path):
        user = tmp_path / "custom" / "settings.yaml"
        user.parent.mkdir(parents=True)
        user.write_text("vocab_count: 15\n")
        cfg = load_config(user)
        assert cfg.vocab_count == 15
        assert cfg.article_word_limit == 100


class TestFirstRun:
    def test_creates_user_config_from_default(self, tmp_path: Path):
        default = tmp_path / "config_default.yaml"
        default.write_text("vocab_count: 8\narticle_word_limit: 200\n")
        user = tmp_path / "config.yaml"
        cfg = load_config(user, default_path=default)
        assert user.is_file()
        assert user.read_text() == default.read_text()
        assert cfg.article_word_limit == 200
        assert cfg.vocab_count == 8

    def test_creates_nested_dir_when_missing(self, tmp_path: Path):
        default = tmp_path / "config_default.yaml"
        default.write_text("vocab_count: 3\n")
        user = tmp_path / "sub" / "config.yaml"
        cfg = load_config(user, default_path=default)
        assert user.is_file()
        assert cfg.article_word_limit == 100

    def test_no_default_falls_back_to_hardcoded(self, tmp_path: Path):
        user = tmp_path / "config.yaml"
        cfg = load_config(user, default_path=tmp_path / "nonexistent.yaml")
        assert cfg.article_word_limit == 100
        assert not user.exists()


class TestDefaults:
    def test_admin_email_default(self):
        assert DaglasConfig().admin_email == ""

    def test_llm_backend_default_mlx(self):
        assert DaglasConfig().llm_backend == "mlx"
