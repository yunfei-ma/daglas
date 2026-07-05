#!/usr/bin/env python3
"""Discover sitemaps for a Swedish news website and select the best
candidate (news sitemap > fresh small sitemap > archive). Optionally
verify by sampling multiple articles via LLM judgement.

Usage:
    python tools/discover_sitemaps.py svt.se
    python tools/discover_sitemaps.py --verify svt.se
    python tools/discover_sitemaps.py --sample-count 10 --verify svt.se
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MAX_INDEX_CHILDREN = 15

PAYWALL_PHRASES = [
    "prenumerera",
    "logga in",
    "betala",
    "plus",
    "prenumerant",
    "för att läsa mer",
    "för att se mer",
    "konto",
    "inloggning",
    "teckna",
]

COMMON_SITEMAP_PATHS = [
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap/sitemap.xml",
    "/news-sitemap.xml",
    "/sitemaps/sitemap.xml",
    "/sitemap/sitemap-index.xml",
]


@dataclass
class LlmVerdict:
    suitable: bool
    reason: str = ""


@dataclass
class FlatSitemapInfo:
    url: str
    entry_count: int
    date_range: tuple[str | None, str | None] = ("", "")
    sample_titles: list[str] = field(default_factory=list)
    has_news_namespace: bool = False
    llm_verdict: LlmVerdict | None = None
    verify_passed: int = 0
    verify_total: int = 0


@dataclass
class DiscoveryResult:
    found_via_robots: list[FlatSitemapInfo] = field(default_factory=list)
    found_via_probing: list[FlatSitemapInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def normalise_input(raw: str) -> str:
    raw = raw.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    return f"{parsed.scheme}://{parsed.netloc}"


def discover_via_robots_txt(base_url: str, client: httpx.Client) -> list[str]:
    urls: list[str] = []
    try:
        resp = client.get(f"{base_url}/robots.txt", timeout=15)
        if resp.status_code == 200:
            for line in resp.text.splitlines():
                stripped = line.strip()
                if stripped.lower().startswith("sitemap:"):
                    url = stripped.split(":", 1)[1].strip()
                    if url:
                        urls.append(url)
    except Exception as e:
        logger.debug("robots.txt fetch failed: %s", e)
    return urls


def probe_common_paths(
    base_url: str, client: httpx.Client, paths: list[str] | None = None
) -> list[str]:
    urls: list[str] = []
    for path in paths or COMMON_SITEMAP_PATHS:
        url = f"{base_url}{path}"
        try:
            resp = client.get(url, timeout=15)
            if resp.status_code == 200 and _looks_like_xml(resp.text):
                urls.append(url)
        except Exception:
            pass
    return urls


def _looks_like_xml(text: str) -> bool:
    return bool(re.search(r"<\?xml\s+version", text, re.IGNORECASE)) or bool(
        re.search(r"<urlset|<sitemapindex", text)
    )


def fetch_and_classify(url: str, client: httpx.Client) -> list[tuple[str, bool]]:
    resp = client.get(url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "xml")

    if soup.find("sitemapindex"):
        children: list[tuple[str, bool]] = []
        for sm in soup.find_all("sitemap"):
            loc = sm.find("loc")
            if loc and loc.text:
                children.append((loc.text.strip(), True))
        return children

    if soup.find("urlset"):
        return [(url, False)]

    return []


def summarise_flat(url: str, client: httpx.Client) -> FlatSitemapInfo:
    resp = client.get(url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "xml")

    entries = soup.find_all("url")
    oldest: str | None = None
    newest: str | None = None
    titles: list[str] = []
    has_news = False

    for entry in entries:
        news = entry.find("news:news")
        if news is not None:
            has_news = True
            pub_tag = news.find("news:publication_date")
            pub_date: str | None = (
                pub_tag.text.strip() if pub_tag and pub_tag.text else None
            )
            title_tag = news.find("news:title")
            if title_tag and title_tag.text and len(titles) < 3:
                titles.append(title_tag.text.strip())
        else:
            pub_date = None

        if pub_date is None:
            lastmod_tag = entry.find("lastmod")
            if lastmod_tag and lastmod_tag.text:
                pub_date = lastmod_tag.text.strip()

        if pub_date:
            if oldest is None or pub_date < oldest:
                oldest = pub_date
            if newest is None or pub_date > newest:
                newest = pub_date

    return FlatSitemapInfo(
        url=url,
        entry_count=len(entries),
        date_range=(oldest, newest),
        sample_titles=titles,
        has_news_namespace=has_news,
    )


ARCHIVE_ENTRY_THRESHOLD = 5000

ARCHIVE_URL_KEYWORDS = [
    "archive",
    "old",
    "page",
    "tag",
    "category",
    "kategori",
    "arkiv",
]


def _newest_date(info: FlatSitemapInfo) -> date:
    _, newest = info.date_range
    if newest:
        try:
            return date.fromisoformat(newest[:10])
        except (ValueError, TypeError):
            pass
    return date.min


def _score_sitemap(info: FlatSitemapInfo) -> tuple:
    is_news = 1 if info.has_news_namespace else 0
    not_archive = 1 if info.entry_count < ARCHIVE_ENTRY_THRESHOLD else 0
    url_has_archive_keyword = any(kw in info.url.lower() for kw in ARCHIVE_URL_KEYWORDS)
    return (is_news, not_archive, not url_has_archive_keyword, _newest_date(info))


def _select_best(items: list[FlatSitemapInfo]) -> FlatSitemapInfo | None:
    if not items:
        return None
    return max(items, key=_score_sitemap)


def _has_paywall_indicators(text: str) -> bool:
    lower = text[:300].lower()
    for phrase in PAYWALL_PHRASES:
        if phrase in lower:
            return True
    return False


def _judge_article(
    article_url: str,
    client: httpx.Client,
    llm_provider,
    prompt_template: str,
) -> LlmVerdict:
    """Fetch a single article and ask the LLM whether it is suitable.
    Returns LlmVerdict with suitable=True/False and a reason string.
    """
    try:
        article_resp = client.get(article_url, timeout=30)
        article_resp.raise_for_status()
    except Exception as e:
        return LlmVerdict(suitable=False, reason=f"fetch failed: {e}")

    import trafilatura

    html = article_resp.text
    raw = trafilatura.extract(
        html, output_format="json", include_images=False, with_metadata=True
    )
    if not raw:
        return LlmVerdict(suitable=False, reason="trafilatura returned no text")

    import json

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return LlmVerdict(suitable=False, reason="trafilatura parse failed")

    text = (data or {}).get("text", "")
    if not text or len(text) < 50:
        return LlmVerdict(suitable=False, reason="text too short")

    if _has_paywall_indicators(text):
        return LlmVerdict(suitable=False, reason="paywall detected")

    user_prompt = prompt_template.replace("{article_text}", text[:2000])

    try:
        response = llm_provider.prompt(
            system="You are a helpful assistant that evaluates Swedish articles.",
            user=user_prompt,
        )
    except Exception as e:
        return LlmVerdict(suitable=False, reason=f"LLM call failed: {e}")

    if response is None:
        return LlmVerdict(suitable=False, reason="LLM returned no response")

    response = response.strip().lower()
    if response.startswith("yes"):
        reason = response[3:].lstrip(": ").strip() if ":" in response else ""
        return LlmVerdict(suitable=True, reason=reason)
    return LlmVerdict(
        suitable=False,
        reason=response[2:].lstrip(": ").strip() if ":" in response else response,
    )


def verify_with_llm(
    sitemap_url: str,
    client: httpx.Client,
    llm_provider,
    sample_count: int = 5,
    prompt_path: str = "",
) -> tuple[list[LlmVerdict], int, int]:
    resp = client.get(sitemap_url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "xml")

    locs = soup.find_all("loc")
    if not locs:
        return [], 0, 0

    article_urls = [loc.text.strip() for loc in locs if loc.text]
    article_urls = article_urls[:sample_count]

    prompt_file = (
        Path(prompt_path)
        if prompt_path
        else (Path(__file__).resolve().parent.parent / "prompts" / "sitemap_verify.md")
    )
    prompt_template = prompt_file.read_text(encoding="utf-8")

    verdicts: list[LlmVerdict] = []
    passed = 0
    for url in article_urls:
        v = _judge_article(url, client, llm_provider, prompt_template)
        verdicts.append(v)
        if v.suitable:
            passed += 1

    return verdicts, passed, len(article_urls)


def discover_sitemaps(
    domain: str,
    *,
    user_agent: str = "daglas-sitemap-discover/1.0",
    max_depth: int = 3,
    verify: bool = False,
    sample_count: int = 5,
    probe_paths: list[str] | None = None,
    llm_provider=None,
    prompt_path: str = "",
    client: httpx.Client | None = None,
) -> DiscoveryResult:
    result = DiscoveryResult()
    base_url = normalise_input(domain)

    if client is None:
        client = httpx.Client(
            headers={"User-Agent": user_agent}, timeout=30, follow_redirects=True
        )
        own_client = True
    else:
        own_client = False

    try:
        robots_urls = discover_via_robots_txt(base_url, client)
        robots_flat: list[FlatSitemapInfo] = []
        for url in robots_urls:
            _process_sitemap(url, client, max_depth, robots_flat, result.errors)

        probed_urls = probe_common_paths(base_url, client, probe_paths)
        probed_flat: list[FlatSitemapInfo] = []
        for url in probed_urls:
            if url not in robots_urls:
                _process_sitemap(url, client, max_depth, probed_flat, result.errors)

        result.found_via_robots = robots_flat
        result.found_via_probing = probed_flat

        if verify and llm_provider:
            all_flat = robots_flat + probed_flat
            if all_flat:
                best = _select_best(all_flat)
                if best:
                    verdicts, passed, total = verify_with_llm(
                        best.url,
                        client,
                        llm_provider,
                        sample_count=sample_count,
                        prompt_path=prompt_path,
                    )
                    best.llm_verdict = verdicts[0] if verdicts else None
                    best.verify_passed = passed
                    best.verify_total = total
    finally:
        if own_client:
            client.close()

    return result


def _process_sitemap(
    url: str,
    client: httpx.Client,
    max_depth: int,
    flat_collector: list[FlatSitemapInfo],
    errors: list[str],
    visited: set[str] | None = None,
    depth: int = 0,
) -> None:
    if visited is None:
        visited = set()
    if url in visited:
        return
    if depth > max_depth:
        return
    visited.add(url)

    try:
        classified = fetch_and_classify(url, client)
    except Exception as e:
        errors.append(f"{url}: classify failed: {e}")
        return

    for child_url, is_index in classified[:MAX_INDEX_CHILDREN]:
        if is_index:
            _process_sitemap(
                child_url,
                client,
                max_depth,
                flat_collector,
                errors,
                visited,
                depth + 1,
            )
        else:
            try:
                info = summarise_flat(child_url, client)
                flat_collector.append(info)
            except Exception as e:
                errors.append(f"{child_url}: summarise failed: {e}")


def _print_results(result: DiscoveryResult, domain: str, verify: bool) -> None:
    print(f"\n=== Sitemaps for {domain} ===\n")

    if result.found_via_robots:
        print("-- Discovered via robots.txt --")
        _print_flat_list(result.found_via_robots, indent=2)

    if result.found_via_probing:
        print("-- Discovered via probing --")
        _print_flat_list(result.found_via_probing, indent=2)

    if verify:
        all_flat = result.found_via_robots + result.found_via_probing
        if all_flat:
            best = _select_best(all_flat)
            if best and best.llm_verdict:
                print("-- LLM verification (--verify) --")
                print(f"  Sampled from: {best.url}")
                print(
                    f"  Articles verified: {best.verify_passed}/{best.verify_total} passed"
                )
                v = best.llm_verdict
                verdict_str = "yes" if v.suitable else "no"
                print(f"  First article verdict: {verdict_str} — {v.reason}")
            else:
                print("-- LLM verification (--verify) --")
                print("  No LLM provider available. Skipping verification.")
        else:
            print("-- LLM verification (--verify) --")
            print("  No flat sitemaps to verify.")

    if result.errors:
        print("\n-- Errors --")
        for err in result.errors:
            print(f"  {err}")

    all_flat = result.found_via_robots + result.found_via_probing
    if all_flat:
        best = _select_best(all_flat)
        if best:
            print("\n---")
            print("\nSuggested config.yaml entry:\n")
            print(
                f"  - name: {urlparse(domain if '://' in domain else 'https://' + domain).netloc.split('.')[0]}"
            )
            print(f"    sitemap: {best.url}")
            print("    max_age_hours: 48\n")


def _print_flat_list(items: list[FlatSitemapInfo], indent: int = 0) -> None:
    prefix = " " * indent
    for info in items:
        print(f"{prefix}{info.url}")
        ns_tag = " [NEWS]" if info.has_news_namespace else ""
        print(f"{prefix}  Entries: {info.entry_count}{ns_tag}", end="")
        oldest, newest = info.date_range
        if oldest and newest:
            print(f"    Dates: {oldest[:10]} .. {newest[:10]}")
        else:
            print()
        if info.sample_titles:
            samples = ", ".join(f'"{t}"' for t in info.sample_titles)
            print(f"{prefix}  Samples: {samples}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Discover flat sitemap URLs for a Swedish news website"
    )
    parser.add_argument("domain", help="Domain or URL (e.g. svt.se)")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify sample article via LLM",
    )
    parser.add_argument(
        "--sample-count",
        type=int,
        default=5,
        help="Number of articles to sample when verifying (default: 5)",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=3,
        help="Max sitemap index recursion depth (default: 3)",
    )
    parser.add_argument(
        "--llm-backend",
        default="",
        help="LLM backend: ollama, mlx, or llamacpp (default: ollama)",
    )
    args = parser.parse_args(argv)

    llm_provider = None
    if args.verify:
        import daglas.config as daglas_config

        if daglas_config.config is None:
            daglas_config.config = daglas_config.load_config()
        if args.llm_backend:
            daglas_config.config.llm_backend = args.llm_backend

        from daglas.lesson.llm import create_llm

        llm_provider = create_llm(daglas_config.config)

    result = discover_sitemaps(
        args.domain,
        max_depth=args.max_depth,
        sample_count=args.sample_count,
        verify=args.verify,
        llm_provider=llm_provider,
    )

    _print_results(result, args.domain, args.verify)

    all_flat = result.found_via_robots + result.found_via_probing
    if not all_flat:
        return 1

    if args.verify:
        best = _select_best(all_flat)
        if best and best.verify_total > 0:
            majority = best.verify_passed > best.verify_total / 2
            if not majority:
                return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
