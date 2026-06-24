#!/usr/bin/env python3
"""Check date extraction for a given article URL.

Usage:
    python3 scripts/check_date_extraction.py <url>

Shows:
    - What trafilatura extracts (including date)
    - What HTML meta/date indicators are present
    - What extract_article() produces
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import trafilatura
from bs4 import BeautifulSoup

from daglas.context_fetcher import extract_article


def scan_html_date_indicators(soup: BeautifulSoup) -> None:
    """Print all machine-readable date indicators found in HTML."""
    indicators: list[str] = []

    for meta in soup.find_all("meta", attrs={"property": "article:published_time"}):
        indicators.append(
            f"  <meta property='article:published_time'> content={meta.get('content')}"
        )
    for meta in soup.find_all("meta", attrs={"name": "date"}):
        indicators.append(f"  <meta name='date'> content={meta.get('content')}")
    for meta in soup.find_all("meta", attrs={"itemprop": "datePublished"}):
        indicators.append(
            f"  <meta itemprop='datePublished'> content={meta.get('content')}"
        )

    for tag in soup.find_all("time"):
        dt = tag.get("datetime")
        text = tag.get_text(strip=True)[:80]
        indicators.append(f"  <time> datetime={dt} text='{text}'")

    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                for key in ("datePublished", "dateModified", "dateCreated"):
                    if key in item:
                        indicators.append(f"  JSON-LD: {key} = {item[key]}")
        except json.JSONDecodeError:
            pass

    if not indicators:
        print("  (none found)")
    else:
        for line in indicators:
            print(line)


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/check_date_extraction.py <url>")
        return 1

    url = sys.argv[1]

    print(f"Fetching: {url}")
    try:
        resp = httpx.get(
            url, timeout=30, headers={"User-Agent": "daglas/1.0"}, follow_redirects=True
        )
        resp.raise_for_status()
    except httpx.RequestError as e:
        print(f"  Request failed: {e}")
        return 1

    html = resp.text
    print(f"Status: {resp.status_code}, Size: {len(html)} bytes")
    print()

    print("=== Trafilatura extraction ===")
    raw = trafilatura.extract(
        html,
        output_format="json",
        include_images=False,
        include_comments=False,
        with_metadata=True,
    )
    if raw:
        result = json.loads(raw)
        for field in ("title", "date", "author", "language", "description"):
            val = result.get(field)
            print(f"  {field}: {val!r}")
        print(f"  categories: {result.get('categories')}")
        print(f"  tags: {result.get('tags')}")
        print(f"  text length: {len(result.get('text', '') or '')}")
    else:
        print("  (trafilatura returned nothing)")
    print()

    print("=== HTML date indicators ===")
    soup = BeautifulSoup(html, "lxml")
    scan_html_date_indicators(soup)
    print()

    print("=== extract_article() result ===")
    article = extract_article(url, html)
    print(f"  publish_date: {article.publish_date!r}")
    print(f"  title:        {article.title!r}")
    print(f"  source:       {article.source!r}")
    print()

    if article.publish_date:
        print("OK — date captured")
    else:
        print("MISSING — no date; would need sitemap backfill")

    return 0


if __name__ == "__main__":
    sys.exit(main())
