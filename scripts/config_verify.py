#!/usr/bin/env python3
"""Verify config loading with real config.yaml and config_default.yaml."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daglas import config as daglas_config
from daglas.config import DEFAULT_CONFIG_PATH, USER_CONFIG_PATH, load_config


def main() -> int:
    print(
        f"Default config  : {DEFAULT_CONFIG_PATH} ({'exists' if DEFAULT_CONFIG_PATH.is_file() else 'missing'})"
    )
    print(
        f"User config     : {USER_CONFIG_PATH} ({'exists' if USER_CONFIG_PATH.is_file() else 'missing'})"
    )

    daglas_config.config = load_config()
    cfg = daglas_config.config

    print(
        f"\nDaglasConfig fields ({len([f for f in dir(cfg) if not f.startswith('_')])}):"
    )
    print(f"  article_word_limit             = {cfg.article_word_limit}")
    print(f"  lesson_level                   = {cfg.lesson_level!r}")
    print(f"  vocab_count                    = {cfg.vocab_count}")
    print(f"  sources                        = {cfg.sources}")
    print(f"  llm_model                      = {cfg.llm_model!r}")
    print(f"  llm_endpoint                   = {cfg.llm_endpoint!r}")
    print(f"  smtp_host                      = {cfg.smtp_host!r}")
    print(f"  smtp_port                      = {cfg.smtp_port}")
    print(f"  from_address                   = {cfg.from_address!r}")
    print(f"  to_addresses                   = {cfg.to_addresses}")
    print(f"  imap_host                      = {cfg.imap_host!r}")
    print(f"  imap_port                      = {cfg.imap_port}")
    print(f"  email_receiver_poll_interval   = {cfg.email_receiver_poll_interval}")
    print(
        f"  email_sender_queue_immediate   = {cfg.email_sender_queue_immediate_interval}"
    )
    print(
        f"  email_sender_queue_scheduled   = {cfg.email_sender_queue_scheduled_interval}"
    )
    print(f"  context_fetcher_poll_interval  = {cfg.context_fetcher_poll_interval}")
    print(f"  fetch_time                     = {cfg.fetch_time!r}")
    print(f"  send_time                      = {cfg.send_time!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
