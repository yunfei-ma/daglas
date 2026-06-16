from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

import httpx
import trafilatura
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)


@dataclass
class Article:
    url: str = ""
    title: str = ""
    body: str = ""
    publish_date: str | None = None
    updated_date: str | None = None
    source: str = ""
    category: str | None = None
    tags: list[str] = field(default_factory=list)
    author: str | None = None
    language: str | None = None


@dataclass
class FetchResult:
    articles: list[Article] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def discover_sitemap_urls(sitemap_url: str, client: httpx.Client) -> set[str]:
    resp = client.get(sitemap_url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "xml")
    urls: set[str] = set()

    if soup.find("sitemapindex"):
        for sitemap_tag in soup.find_all("sitemap"):
            loc = sitemap_tag.find("loc")
            if loc and loc.text:
                urls.update(discover_sitemap_urls(loc.text.strip(), client))
    else:
        for url_tag in soup.find_all("url"):
            loc = url_tag.find("loc")
            if loc and loc.text:
                urls.add(loc.text.strip())

    return urls


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


def fetch_context(
    source_configs: list[dict],
    pool,
    *,
    user_agent: str = "daglas/1.0",
    max_articles: int = 0,
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
                urls = discover_sitemap_urls(sitemap_url, client)
            except Exception as e:
                errors.append(f"{sitemap_url}: discovery failed: {e}")
                continue

            for url in urls:
                if url in seen:
                    continue
                seen.add(url)
                if max_articles and len(articles) >= max_articles:
                    break
                try:
                    resp = client.get(url, timeout=30)
                    resp.raise_for_status()
                    article = extract_article(url, resp.text)
                    article.source = source.get("name", _domain_from_url(url))
                    articles.append(article)
                except Exception as e:
                    errors.append(f"{url}: {e}")
            if max_articles and len(articles) >= max_articles:
                break

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

    def _run(self) -> None:
        while not self._stop_event.is_set():
            result = self.fetch_once()
            if result.articles:
                logger.info("Fetched %d article(s)", len(result.articles))
            for err in result.errors:
                logger.warning("Fetch error: %s", err)
            self._stop_event.wait(timeout=self._poll_interval)
