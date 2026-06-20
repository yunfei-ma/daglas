from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
import trafilatura
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)


class ArticleStore(Protocol):
    def seen_urls(self, lookback_days: int) -> set[str]: ...
    def store_article(self, article: dict) -> None: ...


@dataclass
class SitemapEntry:
    url: str
    publish_date: str | None = None
    title: str = ""


@dataclass
class Article:
    publish_date: str | None = None
    title: str = ""
    tags: list[str] = field(default_factory=list)
    body: str = ""
    source: str = ""
    url: str = ""


@dataclass
class FetchResult:
    articles: list[Article] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class SiteThreadContext:
    """Per-site container for the fetch pipeline. Each source gets one context."""

    def __init__(
        self,
        source_config: dict,
        store: ArticleStore,
        *,
        seen: set[str] | None = None,
    ):
        self.source_config = source_config
        self.store = store
        self.seen: set[str] = seen if seen is not None else set()
        self.errors: list[str] = []
        self.status: str = "PENDING"
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        _t0 = time.perf_counter()
        self.status = "RUNNING"

        if self._stop_event.is_set():
            self.status = "STOPPED"
            return

        self._load_seen_urls()

        with httpx.Client(
            headers={"User-Agent": "daglas/1.0"}, timeout=30, follow_redirects=True
        ) as client:
            entries = self._fetch_entries(client)
            if entries is None:
                return
            self._fetch_articles(entries, client)

        self.status = "COMPLETED"
        _elapsed = time.perf_counter() - _t0
        source_name = self.source_config.get(
            "name", _domain_from_url(self.source_config["sitemap"])
        )
        logger.info("Site %s: completed in %.1fs", source_name, _elapsed)

    def _load_seen_urls(self) -> None:
        try:
            self.seen.update(self.store.seen_urls(7))
        except Exception:
            logger.warning("Failed to load seen URLs from pool", exc_info=True)

    def _fetch_entries(self, client: httpx.Client) -> list[SitemapEntry] | None:
        _t0 = time.perf_counter()
        sitemap_url = self.source_config["sitemap"]
        source_name = self.source_config.get("name", _domain_from_url(sitemap_url))
        try:
            entries = read_sitemap_entries(sitemap_url, client)
        except Exception as e:
            logging.error("%s: %s", source_name, e)
            self.status = "FAILED"
            return None

        _elapsed = time.perf_counter() - _t0
        logger.info(
            "Sitemap %s: fetched %d entries in %.1fs",
            source_name,
            len(entries),
            _elapsed,
        )

        source_max_age = self.source_config.get("max_age_hours", 0) or 0
        if source_max_age > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=source_max_age)
            entries = [
                e
                for e in entries
                if e.publish_date is None or _parse_date(e.publish_date) >= cutoff
            ]
        entries.sort(key=_entry_sort_key, reverse=True)
        return entries

    def _fetch_articles(
        self, entries: list[SitemapEntry], client: httpx.Client
    ) -> None:
        source_name = self.source_config.get(
            "name", _domain_from_url(self.source_config["sitemap"])
        )
        max_daily = self.source_config.get("max_daily_articles", 0) or 0

        for i, entry in enumerate(entries):
            if self._stop_event.is_set():
                self.status = "STOPPED"
                return
            if max_daily and i >= max_daily:
                break
            if entry.url in self.seen:
                continue
            self.seen.add(entry.url)
            self._process_entry(entry, client, source_name)

        if self.errors:
            logging.warning("%s: %d article(s) failed", source_name, len(self.errors))

    def _process_entry(
        self, entry: SitemapEntry, client: httpx.Client, source_name: str
    ) -> None:
        _t0 = time.perf_counter()
        try:
            article = _fetch_article(entry, client)
            if not article.title and entry.title:
                article.title = entry.title
            _backfill_article_date(article, entry)
            article.source = source_name
            self.store.store_article(article.__dict__)
            _elapsed = time.perf_counter() - _t0
            logger.debug(
                "Fetched %s in %.1fms publish_date=%s",
                article.url,
                _elapsed * 1000,
                article.publish_date,
            )
        except Exception as e:
            self.errors.append(f"{entry.url}: {e}")


def _parse_url_tag(url_tag: Any) -> SitemapEntry | None:
    loc = url_tag.find("loc")
    if not loc or not loc.text:
        return None

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

    return SitemapEntry(url=url, publish_date=publish_date, title=title)


def read_sitemap_entries(sitemap_url: str, client: httpx.Client) -> list[SitemapEntry]:
    _t0 = time.perf_counter()
    resp = client.get(sitemap_url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "xml")
    logger.debug(
        "read_sitemap_entries: HTTP+parse took %.1fms for %s",
        (time.perf_counter() - _t0) * 1000,
        sitemap_url,
    )

    if soup.find("sitemapindex"):
        raise ValueError(
            f"{sitemap_url} is a sitemap index, not a flat urlset — "
            "configure a flat sitemap URL directly"
        )

    entries: list[SitemapEntry] = []
    for url_tag in soup.find_all("url"):
        entry = _parse_url_tag(url_tag)
        if entry is not None:
            entries.append(entry)
    return entries


def _scan_html_for_date(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")

    for meta in soup.find_all("meta", attrs={"property": "article:published_time"}):
        if meta.get("content"):
            return meta["content"].strip()

    for meta in soup.find_all("meta", attrs={"name": "article:published_time"}):
        if meta.get("content"):
            return meta["content"].strip()

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string) if script.string else None
        except json.JSONDecodeError:
            continue
        if not data:
            continue
        for key in ("datePublished", "dateModified"):
            val = data.get(key)
            if val:
                return str(val).strip()

    for time_tag in soup.find_all("time"):
        if time_tag.get("datetime"):
            return time_tag["datetime"].strip()

    for meta in soup.find_all("meta", attrs={"property": "article:modified_time"}):
        if meta.get("content"):
            return meta["content"].strip()

    return None


def extract_article(url: str, html: str) -> Article:
    raw = trafilatura.extract(
        html, output_format="json", include_images=False, with_metadata=True
    )
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
        publish_date = _safe_str(result.get("date"))
        if not publish_date:
            publish_date = _scan_html_for_date(html)
        return Article(
            url=url,
            title=title,
            body=result.get("text", ""),
            publish_date=publish_date,
            source=_domain_from_url(url),
            tags=result.get("tags") or [],
        )
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    body_tag = soup.find("article") or soup.find("main") or soup.find("body")
    body = body_tag.get_text(strip=True) if body_tag else ""
    publish_date = _scan_html_for_date(html)
    return Article(
        url=url,
        title=title,
        body=body,
        publish_date=publish_date,
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
    # Python 3.9 does not support Z suffix — normalise to +00:00
    if date_str.endswith("Z"):
        date_str = date_str[:-1] + "+00:00"
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


def _read_and_filter_sitemap(
    source: dict, client: httpx.Client, global_max_age: int
) -> list[SitemapEntry]:
    entries = read_sitemap_entries(source["sitemap"], client)
    source_max_age = source.get("max_age_hours", 0) or global_max_age
    if source_max_age > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=source_max_age)
        entries = [
            e
            for e in entries
            if e.publish_date is None or _parse_date(e.publish_date) >= cutoff
        ]
    entries.sort(key=_entry_sort_key, reverse=True)
    return entries


def _backfill_article_date(article: Article, entry: SitemapEntry) -> None:
    if not article.publish_date and entry.publish_date:
        article.publish_date = entry.publish_date
        logger.debug(
            "DATE_BACKFILL sitemap=%s url=%s",
            entry.publish_date,
            entry.url,
        )
    elif not article.publish_date and not entry.publish_date:
        logger.warning(
            "DATE_MISSING no trafilatura date and no sitemap date url=%s",
            entry.url,
        )


def _fetch_article(entry: SitemapEntry, client: httpx.Client) -> Article:
    resp = client.get(entry.url, timeout=30)
    resp.raise_for_status()
    return extract_article(entry.url, resp.text)


def fetch_context(
    source_configs: list[dict],
    pool: ArticleStore,
    *,
    user_agent: str = "daglas/1.0",
    max_daily_articles: int = 0,
    max_age_hours: int = 0,
) -> FetchResult:
    _t0 = time.perf_counter()
    seen: set[str] = set()
    articles: list[Article] = []
    errors: list[str] = []

    with httpx.Client(
        headers={"User-Agent": user_agent}, timeout=30, follow_redirects=True
    ) as client:
        for source in source_configs:
            sitemap_url = source.get("sitemap", "")
            if not sitemap_url:
                continue
            try:
                entries = _read_and_filter_sitemap(source, client, max_age_hours)
            except Exception as e:
                errors.append(f"{sitemap_url}: parse failed: {e}")
                continue

            source_max = source.get("max_daily_articles", 0) or 0
            if source_max:
                entries = entries[:source_max]

            for entry in entries:
                if entry.url in seen:
                    continue
                seen.add(entry.url)
                try:
                    article = _fetch_article(entry, client)
                    if not article.title and entry.title:
                        article.title = entry.title
                    _backfill_article_date(article, entry)
                    article.source = source.get("name", _domain_from_url(entry.url))
                    articles.append(article)
                except Exception as e:
                    errors.append(f"{entry.url}: {e}")
            if max_daily_articles and len(articles) >= max_daily_articles:
                break

    articles.sort(key=lambda a: a.publish_date or "", reverse=True)
    deduped = deduplicate(articles)

    for article in deduped:
        pool.store_article(article.__dict__)

    _elapsed = time.perf_counter() - _t0
    logger.info(
        "fetch_context: %d articles from %d sources in %.1fs",
        len(deduped),
        len(source_configs),
        _elapsed,
    )
    return FetchResult(articles=deduped, errors=errors)


def _domain_from_url(url: str) -> str:
    return urlparse(url).netloc


def _safe_str(val: object) -> str | None:
    if val is None:
        return None
    return str(val)


class ContextFetcherDaemon:
    def __init__(
        self,
        source_configs: list[dict],
        pool: ArticleStore,
        *,
        fetch_time: str = "06:00",
        poll_interval: int = 86400,
        max_site_threads: int = 12,
    ):
        self._source_configs = source_configs
        self._pool = pool
        self._fetch_time = fetch_time
        self._poll_interval = poll_interval
        self._max_site_threads = max_site_threads
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._contexts: dict[str, SiteThreadContext] = {}

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

    def stop_context(self, source_name: str) -> None:
        """Stop a specific site thread. No-op if already done or unknown."""
        ctx = self._contexts.get(source_name)
        if ctx:
            logger.info("Stopping context: %s", source_name)
            ctx.stop()

    def status(self, source_name: str | None = None) -> dict:
        """Return status dict for one or all contexts."""
        if source_name:
            ctx = self._contexts.get(source_name)
            if not ctx:
                return {"error": "source not found"}
            return {
                "status": ctx.status,
                "articles_count": len(ctx.seen),
                "errors": list(ctx.errors),
            }
        return {
            name: {
                "status": ctx.status,
                "articles_count": len(ctx.seen),
                "errors": list(ctx.errors),
            }
            for name, ctx in self._contexts.items()
        }

    def fetch_once(self) -> None:
        """Orchestrate per-site threads. Each context stores articles directly to pool."""
        _t0 = time.perf_counter()
        self._contexts = {}
        for cfg in self._source_configs:
            name = cfg.get("name", _domain_from_url(cfg["sitemap"]))
            self._contexts[name] = SiteThreadContext(
                source_config=cfg,
                store=self._pool,
            )

        with ThreadPoolExecutor(max_workers=self._max_site_threads) as executor:
            future_to_name = {
                executor.submit(ctx.run): name for name, ctx in self._contexts.items()
            }
            for future in as_completed(future_to_name):
                name = future_to_name[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error("%s: thread failed: %s", name, e)

        _elapsed = time.perf_counter() - _t0
        logger.info("fetch_once: all sources complete in %.1fs", _elapsed)

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

            self.fetch_once()
