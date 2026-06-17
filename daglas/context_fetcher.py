from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
import trafilatura
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)


@dataclass
class SitemapEntry:
    url: str
    publish_date: str | None = None
    title: str = ""


@dataclass
class Article:
    publish_date: str | None = None
    url: str = ""
    title: str = ""
    body: str = ""
    source: str = ""
    updated_date: str | None = None
    category: str | None = None
    tags: list[str] = field(default_factory=list)
    author: str | None = None
    language: str | None = None


@dataclass
class FetchResult:
    articles: list[Article] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def read_sitemap_entries(sitemap_url: str, client: httpx.Client) -> list[SitemapEntry]:
    resp = client.get(sitemap_url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "xml")

    if soup.find("sitemapindex"):
        raise ValueError(
            f"{sitemap_url} is a sitemap index, not a flat urlset — "
            "configure a flat sitemap URL directly"
        )

    entries: list[SitemapEntry] = []
    for url_tag in soup.find_all("url"):
        loc = url_tag.find("loc")
        if not loc or not loc.text:
            continue

        url = loc.text.strip()
        publish_date: str | None = None
        title = ""

        news = url_tag.find("news:news")
        if news:
            pub_date_tag = news.find("news:publication_date")
            if pub_date_tag and pub_date_tag.text:
                publish_date = pub_date_tag.text.strip()
            title_tag = news.find("news:title")
            if title_tag and title_tag.text:
                title = title_tag.text.strip()

        if publish_date is None:
            lastmod = url_tag.find("lastmod")
            if lastmod and lastmod.text:
                publish_date = lastmod.text.strip()

        entries.append(SitemapEntry(url=url, publish_date=publish_date, title=title))

    return entries


def extract_article(url: str, html: str) -> Article:
    import json

    raw = trafilatura.extract(html, output_format="json", include_images=False)
    if raw:
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = None
    else:
        result = None

    if result and result.get("text"):
        title = result.get("title") or ""
        if not title:
            soup = BeautifulSoup(html, "lxml")
            if soup.title and soup.title.string:
                title = soup.title.string.strip()
        return Article(
            url=url,
            title=title,
            body=result.get("text", ""),
            publish_date=_safe_str(result.get("date")),
            author=_safe_str(result.get("author")),
            source=_domain_from_url(url),
            category=_safe_str(result.get("categories", [None])[0])
            if result.get("categories")
            else None,
            tags=result.get("tags") or [],
            language=_safe_str(result.get("language")),
        )

    soup = BeautifulSoup(html, "lxml")
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    body_tag = soup.find("article") or soup.find("main") or soup.find("body")
    body = body_tag.get_text(strip=True) if body_tag else ""
    return Article(
        url=url,
        title=title,
        body=body,
        source=_domain_from_url(url),
    )


def deduplicate(articles: list[Article]) -> list[Article]:
    seen: set[str] = set()
    result: list[Article] = []
    for article in articles:
        if article.url not in seen:
            seen.add(article.url)
            result.append(article)
    return result


def _parse_date(date_str: str) -> datetime:
    naive = datetime.fromisoformat(date_str)
    if naive.tzinfo is None:
        return naive.replace(tzinfo=timezone.utc)
    return naive


def _entry_sort_key(entry: SitemapEntry) -> datetime:
    if entry.publish_date:
        try:
            return _parse_date(entry.publish_date)
        except (ValueError, TypeError):
            return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def fetch_context(
    source_configs: list[dict],
    pool,
    *,
    user_agent: str = "daglas/1.0",
    max_articles: int = 0,
    max_age_hours: int = 0,
) -> FetchResult:
    seen: set[str] = set()
    articles: list[Article] = []
    errors: list[str] = []

    with httpx.Client(headers={"User-Agent": user_agent}, timeout=30) as client:
        for source in source_configs:
            sitemap_url = source.get("sitemap", "")
            if not sitemap_url:
                continue
            try:
                entries = read_sitemap_entries(sitemap_url, client)
            except Exception as e:
                errors.append(f"{sitemap_url}: parse failed: {e}")
                continue

            source_max_age = source.get("max_age_hours", 0) or max_age_hours
            if source_max_age > 0:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=source_max_age)
                entries = [
                    e
                    for e in entries
                    if e.publish_date is None or _parse_date(e.publish_date) >= cutoff
                ]

            entries.sort(key=_entry_sort_key, reverse=True)

            for entry in entries:
                if entry.url in seen:
                    continue
                seen.add(entry.url)
                if max_articles and len(articles) >= max_articles:
                    break
                try:
                    resp = client.get(entry.url, timeout=30)
                    resp.raise_for_status()
                    article = extract_article(entry.url, resp.text)
                    if not article.title and entry.title:
                        article.title = entry.title
                    article.source = source.get("name", _domain_from_url(entry.url))
                    articles.append(article)
                except Exception as e:
                    errors.append(f"{entry.url}: {e}")
            if max_articles and len(articles) >= max_articles:
                break

    articles.sort(key=lambda a: a.publish_date or "", reverse=True)
    deduped = deduplicate(articles)

    if deduped:
        pool.store_articles([a.__dict__ for a in deduped])

    return FetchResult(articles=deduped, errors=errors)


def _domain_from_url(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).netloc


def _safe_str(val) -> str | None:
    if val is None:
        return None
    return str(val)


class ContextFetcherDaemon:
    def __init__(
        self,
        source_configs: list[dict],
        pool,
        *,
        fetch_time: str = "06:00",
        poll_interval: int = 86400,
        max_articles: int = 3,
    ):
        self._source_configs = source_configs
        self._pool = pool
        self._fetch_time = fetch_time
        self._poll_interval = poll_interval
        self._max_articles = max_articles
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            logger.warning("ContextFetcherDaemon is already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("ContextFetcherDaemon started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("ContextFetcherDaemon stopped")

    def fetch_once(self) -> FetchResult:
        return fetch_context(
            self._source_configs,
            self._pool,
            max_articles=self._max_articles,
        )

    def _seconds_until_fetch_time(self) -> float:
        hour, minute = self._fetch_time.split(":")
        now = datetime.now(timezone.utc)
        fetch_dt = now.replace(
            hour=int(hour), minute=int(minute), second=0, microsecond=0
        )
        if fetch_dt <= now:
            fetch_dt += timedelta(days=1)
        return (fetch_dt - now).total_seconds()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            wait = self._seconds_until_fetch_time()
            while wait > 0 and not self._stop_event.is_set():
                sleep = min(wait, float(self._poll_interval))
                self._stop_event.wait(timeout=sleep)
                wait -= sleep

            if self._stop_event.is_set():
                break

            result = self.fetch_once()
            if result.articles:
                logger.info("Fetched %d article(s)", len(result.articles))
            for err in result.errors:
                logger.warning("Fetch error: %s", err)
