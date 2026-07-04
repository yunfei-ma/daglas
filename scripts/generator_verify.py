#!/usr/bin/env python3
"""
Integration test: full lesson pipeline against the real backend.

Loads config.yaml as configured — same backend, same endpoint, same model
as ``daglas run --generate``.  Exercises:

1. ``create_llm(cfg)`` — build a real Llm instance
2. ``generate_lesson()`` — prompt the LLM with real templates and article
3. ``format_email()`` — render the response into an Email

Usage:
    python scripts/generator_verify.py

Detects whether a server is already running on the configured endpoint.
If so, connects without spawning a new one.  If not, lets Llm manage
the server lifecycle (mlx_server only).
"""

from __future__ import annotations

import logging
import socket
import sys
from urllib.parse import urlparse

import daglas.config as daglas_config
from daglas.config import load_config
from daglas.lesson.formatter import format_email
from daglas.lesson.generator import generate_lesson
from daglas.lesson.llm import BACKEND_DEFAULTS, create_llm


def _server_alive(endpoint: str) -> bool:
    url = urlparse(endpoint)
    host = url.hostname or "127.0.0.1"
    port = url.port or 8081
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        return s.connect_ex((host, port)) == 0


def main() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)-5s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    daglas_config.config = load_config()
    cfg = daglas_config.config

    backend = cfg.llm_backend or ""

    print(f"Backend: {backend}")
    print(f"Endpoint: {cfg.llm_endpoint or '(default)'}")
    print(f"Model: {cfg.llm_model or '(default)'}")
    print()

    defaults = BACKEND_DEFAULTS.get(backend, {})
    endpoint = cfg.llm_endpoint or defaults.get("endpoint", "http://127.0.0.1:8081/v1")

    alive = _server_alive(endpoint)
    if alive:
        print(
            f"Server already running on {endpoint} — connecting without lifecycle management"
        )

    llm = create_llm(cfg)

    if alive:
        llm._manage_process = False

    articles = [
        {
            "title": "Sverige inför nya regler för kontanter",
            "body": (
                "Från och med nästa år kommer nya regler att införas som "
                "gör det enklare för butiker att vägra kontanter. "
                "Regeringen menar att det är en naturlig utveckling i "
                "det allt mer digitala samhället. Men kritiker varnar "
                "för att äldre och personer på landsbygden kan få "
                "problem om kontanterna försvinner helt. "
                "En utredning visar att endast 10 procent av "
                "befolkningen använder kontanter idag."
            ),
        }
    ]

    print("Generating lesson...")
    lesson_text = generate_lesson(
        llm,
        articles,
        level="beginner",
        vocab_count=5,
    )

    if lesson_text is None:
        print(
            "ERROR: lesson generation returned None (validation rejected the response)"
        )
        print("Check that the server is running and reachable.")
        sys.exit(1)

    email = format_email(lesson_text)

    print("=" * 60)
    print("SUBJECT:", email.subject)
    print("=" * 60)
    print()
    print(email.text_body)
    print()
    print("=" * 60)
    print("HTML length:", len(email.html_body), "chars")
    print("=" * 60)

    llm.close()


if __name__ == "__main__":
    main()
