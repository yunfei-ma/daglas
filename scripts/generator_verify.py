#!/usr/bin/env python3
"""
Integration test: full lesson pipeline against the real backend.

Loads config.yaml as configured — same backend, same endpoint, same model
as ``daglas run --generate``.  Exercises:

1. ``create_llm(cfg)`` — build a real Llm or MlxModel instance
2. ``generate_lesson()`` — prompt the LLM with real templates and article
3. ``format_email()`` — render the response into an Email

Usage:
    python scripts/generator_verify.py
"""

from __future__ import annotations

import logging
import sys

import daglas.config as daglas_config
from daglas.config import load_config
from daglas.lesson.formatter import format_email
from daglas.lesson.generator import generate_lesson
from daglas.lesson.llm import BACKEND_DEFAULTS, create_llm


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
    print(f"Model: {cfg.llm_model or '(default)'}")
    print()

    llm = create_llm(cfg)

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
        print("Check that the LLM backend is available.")
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
