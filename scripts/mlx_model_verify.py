#!/usr/bin/env python3
"""
Proof: real MlxModel generation with mocked Swedish articles.

1. Writes Swedish articles directly to the context pool
2. Loads config, creates MlxModel via create_llm()
3. Runs generate_lesson() then format_email()
4. Prints the result
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import daglas.config as daglas_config
from daglas.config import load_config
from daglas.context_pool import ContextPool
from daglas.lesson.formatter import format_email
from daglas.lesson.generator import generate_lesson
from daglas.lesson.llm import BACKEND_DEFAULTS, create_llm

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)-5s %(message)s",
)


def main():
    daglas_config.config = load_config()
    cfg = daglas_config.config

    pool = ContextPool(data_dir=cfg.data_dir)
    pool.clear()

    articles = [
        {
            "title": "Nya regler för kontanter i Sverige",
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
        },
        {
            "title": "Stockholms tunnelbana får nya tåg",
            "body": (
                "Stockholms lokaltrafik har presenterat de nya tågen som "
                "ska börja rulla i tunnelbanan nästa år. Tågen är "
                "energisnålare och har plats för fler passagerare. "
                "Varje tåg kan ta upp till 1200 personer, vilket är "
                "en ökning med 30 procent jämfört med dagens tåg. "
                "De nya tågen är också utrustade med bättre "
                "informationsskärmar och fler sittplatser."
            ),
        },
        {
            "title": "Svenska elever läser allt mindre böcker",
            "body": (
                "En ny undersökning visar att svenska ungdomar läser "
                "allt mindre böcker på fritiden. Bara 30 procent av "
                "eleverna i årskurs 9 läser en bok i månaden. "
                "Lärare är oroade över utvecklingen och menar att "
                "skärmtid konkurrerar med läsning. "
                "Samtidigt visar forskning att de som läser mycket "
                "har bättre ordförråd och skrivförmåga."
            ),
        },
    ]

    for art in articles:
        pool.store_article(art)
        print(f"  Stored: {art['title']}")

    print(f"\nBackend: {cfg.llm_backend}")
    print(f"Model: {cfg.llm_model or '(default)'}")
    print(f"hf_cache_dir: {cfg.hf_cache_dir or '(not set)'}")
    print()

    print("Creating LLM...")
    llm = create_llm(cfg)
    print(f"  Type: {type(llm).__name__}")
    print(f"  State: {llm.state}")

    print("\nGenerating lesson...")
    lesson_text = generate_lesson(
        llm,
        articles,
        level=cfg.lesson_level,
        vocab_count=cfg.vocab_count,
    )

    if lesson_text is None:
        print("ERROR: lesson generation returned None")
        sys.exit(1)

    email = format_email(lesson_text)

    print("=" * 60)
    print(f"SUBJECT: {email.subject}")
    print("=" * 60)
    print()
    print(email.text_body)
    print()
    print("=" * 60)
    print(f"HTML length: {len(email.html_body)} chars")
    print("=" * 60)

    llm.close()
    print(f"\nState after close: {llm.state}")


if __name__ == "__main__":
    main()
