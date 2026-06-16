from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config_default.yaml"
USER_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@dataclass
class SourceConfig:
    name: str = ""
    sitemap: str = ""


@dataclass
class DaglasConfig:
    # --- Core ---
    max_context_length: int = 500
    article_word_limit: int = 100
    lesson_level: str = "beginner"
    vocab_count: int = 5

    # --- Context sources ---
    sources: list[dict] = field(default_factory=list)

    # --- LLM ---
    llm_model: str = ""
    llm_endpoint: str = ""
    llm_api_key: str = ""

    # --- Email ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_address: str = ""
    to_addresses: list[str] = field(default_factory=list)

    # --- IMAP (inbound subscription requests) ---
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""

    # --- Email receiver ---
    email_receiver_poll_interval: int = 300

    # --- Email sender queue ---
    email_sender_queue_immediate_interval: int = 20
    email_sender_queue_scheduled_interval: int = 300

    # --- Scheduling ---
    fetch_time: str = "06:00"
    context_fetcher_poll_interval: int = 86400  # check once daily after initial fetch
    send_time: str = "07:00"

    # --- Paths ---
    data_dir: str = "data"
    prompts_dir: str = "prompts"


def load_config(
    path: Path | None = None,
    default_path: Path | None = None,
) -> DaglasConfig:
    user_path = path or USER_CONFIG_PATH
    resolved_default_path = default_path or DEFAULT_CONFIG_PATH

    if user_path.is_file():
        with open(user_path) as f:
            raw = yaml.safe_load(f) or {}
        return DaglasConfig(**raw)

    user_path.parent.mkdir(parents=True, exist_ok=True)
    if resolved_default_path.is_file():
        text = resolved_default_path.read_text()
        user_path.write_text(text)
        raw = yaml.safe_load(text) or {}
        return DaglasConfig(**raw)

    return DaglasConfig()


config: DaglasConfig | None = None
