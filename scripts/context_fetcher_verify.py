#!/usr/bin/env python3
"""Verify ContextFetcherDaemon with a real sitemap source."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daglas import config as daglas_config
from daglas.config import load_config
from daglas.context_fetcher import ContextFetcherDaemon
from daglas.context_pool import ContextPool


def main() -> int:
    daglas_config.config = load_config()
    cfg = daglas_config.config

    if not cfg.sources:
        print("No sources configured — using default SVT sitemap for verification.")
        sources = [{"name": "svt", "sitemap": "https://www.svt.se/sitemap.xml"}]
    else:
        sources = cfg.sources

    pool = ContextPool()
    fetcher = ContextFetcherDaemon(
        sources,
        pool,
        fetch_time=cfg.fetch_time,
        poll_interval=cfg.context_fetcher_poll_interval,
        max_articles=3,
    )

    print(f"Fetching from {len(sources)} source(s)...")
    result = fetcher.fetch_once()
    for err in result.errors:
        print(f"  ERROR: {err}")
    print(f"Fetched {len(result.articles)} article(s)")
    for a in result.articles:
        print(f"  - {a.title} ({a.source})")
    pool_articles = pool.retrieve_articles()
    print(f"Stored in pool: {len(pool_articles)} article(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
