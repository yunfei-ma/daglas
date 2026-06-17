import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx

from daglas.context_fetcher import (
    Article,
    ContextFetcherDaemon,
    FetchResult,
    deduplicate,
    extract_article,
    fetch_context,
    read_sitemap_entries,
)

SITEMAP_PLAIN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.se/artikel/1</loc></url>
  <url><loc>https://example.se/artikel/2</loc></url>
</urlset>"""

SITEMAP_NEWS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url>
    <loc>https://example.se/artikel/a</loc>
    <lastmod>2026-06-17T10:00:00+02:00</lastmod>
    <news:news>
      <news:publication>
        <news:name>Test</news:name>
        <news:language>sv</news:language>
      </news:publication>
      <news:publication_date>2026-06-17T08:00:00+02:00</news:publication_date>
      <news:title>Article A Title</news:title>
    </news:news>
  </url>
  <url>
    <loc>https://example.se/artikel/b</loc>
    <lastmod>2026-06-16T10:00:00+02:00</lastmod>
  </url>
  <url>
    <loc>https://example.se/artikel/c</loc>
    <news:news>
      <news:publication>
        <news:name>Test</news:name>
        <news:language>sv</news:language>
      </news:publication>
      <news:publication_date>2026-06-15T12:00:00+02:00</news:publication_date>
      <news:title>Article C Title</news:title>
    </news:news>
  </url>
</urlset>"""

SITEMAP_INDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.se/sitemap1.xml</loc></sitemap>
</sitemapindex>"""

SITEMAP_EMPTY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
</urlset>"""

ARTICLE_HTML = """<html><head>
<meta property="og:title" content="Swedish Article" />
<title>Swedish Article</title></head>
<body><article><h1>Swedish Article</h1>
<p>Detta är en svensk artikel om Stockholm.</p></article></body></html>"""

SHORT_HTML = "<html><body><p>Hej</p></body></html>"


def _mock_client(responses: dict[str, str]) -> httpx.Client:
    def handler(request):
        url = str(request.url)
        if url in responses:
            return httpx.Response(200, text=responses[url])
        return httpx.Response(404)

    transport = MagicMock(spec=httpx.BaseTransport)
    transport.handle_request.side_effect = handler
    return httpx.Client(transport=transport)


class TestReadSitemapEntries:
    def test_news_metadata(self):
        client = _mock_client({"https://example.se/sitemap.xml": SITEMAP_NEWS_XML})
        entries = read_sitemap_entries("https://example.se/sitemap.xml", client)
        assert len(entries) == 3

        a, b, c = entries
        assert a.url == "https://example.se/artikel/a"
        assert a.publish_date == "2026-06-17T08:00:00+02:00"
        assert a.title == "Article A Title"

        assert b.url == "https://example.se/artikel/b"
        assert b.publish_date == "2026-06-16T10:00:00+02:00"
        assert b.title == ""

        assert c.url == "https://example.se/artikel/c"
        assert c.publish_date == "2026-06-15T12:00:00+02:00"
        assert c.title == "Article C Title"

    def test_plain_sitemap(self):
        client = _mock_client({"https://example.se/sitemap.xml": SITEMAP_PLAIN_XML})
        entries = read_sitemap_entries("https://example.se/sitemap.xml", client)
        assert len(entries) == 2
        for e in entries:
            assert e.publish_date is None
            assert e.title == ""

    def test_index_raises(self):
        client = _mock_client({"https://example.se/sitemap.xml": SITEMAP_INDEX_XML})
        import pytest

        with pytest.raises(ValueError, match="sitemap index"):
            read_sitemap_entries("https://example.se/sitemap.xml", client)

    def test_empty(self):
        client = _mock_client({"https://example.se/sitemap.xml": SITEMAP_EMPTY_XML})
        entries = read_sitemap_entries("https://example.se/sitemap.xml", client)
        assert entries == []


class TestExtractArticle:
    def test_extract_article(self):
        article = extract_article("https://example.se/a", ARTICLE_HTML)
        assert article.url == "https://example.se/a"
        assert article.title
        assert article.body

    def test_extract_article_empty_body(self):
        article = extract_article("https://example.se/b", SHORT_HTML)
        assert article.url == "https://example.se/b"

    def test_extract_article_fallback(self):
        html = "<html><body><main><p>Fallback content</p></main></body></html>"
        article = extract_article("https://example.se/c", html)
        assert "Fallback content" in article.body


class TestDeduplicate:
    def test_deduplicate(self):
        articles = [
            Article(url="https://example.se/1"),
            Article(url="https://example.se/2"),
            Article(url="https://example.se/1"),
        ]
        result = deduplicate(articles)
        assert len(result) == 2
        assert result[0].url == "https://example.se/1"
        assert result[1].url == "https://example.se/2"


class TestFetchContext:
    def test_no_sources(self):
        pool = MagicMock()
        result = fetch_context([], pool)
        assert result.articles == []
        assert result.errors == []
        pool.store_articles.assert_not_called()

    def test_sitemap_unreachable(self):
        def handler(request):
            raise httpx.ConnectError("unreachable")

        transport = MagicMock(spec=httpx.BaseTransport)
        transport.handle_request.side_effect = handler
        client = httpx.Client(transport=transport)

        with patch("daglas.context_fetcher.httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value = client
            pool = MagicMock()
            sources = [{"sitemap": "https://example.se/sitemap.xml", "name": "test"}]
            result = fetch_context(sources, pool)
            assert len(result.errors) >= 1
            pool.store_articles.assert_not_called()

    def test_all_fail(self):
        def handler(request):
            raise httpx.ConnectError("fail")

        transport = MagicMock(spec=httpx.BaseTransport)
        transport.handle_request.side_effect = handler
        client = httpx.Client(transport=transport)

        with patch("daglas.context_fetcher.httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value = client
            pool = MagicMock()
            sources = [{"sitemap": "https://a.se/sitemap.xml", "name": "a"}]
            result = fetch_context(sources, pool)
            assert len(result.errors) >= 1
            assert result.articles == []
            pool.store_articles.assert_not_called()

    def test_deduplicates_across_sitemaps(self):
        ARTICLE_HTML_SIMPLE = "<html><body><p>Hej</p></body></html>"
        responses = {
            "https://a.se/sitemap.xml": SITEMAP_PLAIN_XML,
            "https://b.se/sitemap.xml": SITEMAP_PLAIN_XML,
            "https://example.se/artikel/1": ARTICLE_HTML_SIMPLE,
            "https://example.se/artikel/2": ARTICLE_HTML_SIMPLE,
        }
        client = _mock_client(responses)

        with patch("daglas.context_fetcher.httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value = client
            pool = MagicMock()
            sources = [
                {"sitemap": "https://a.se/sitemap.xml", "name": "a"},
                {"sitemap": "https://b.se/sitemap.xml", "name": "b"},
            ]
            result = fetch_context(sources, pool)
            assert len(result.articles) == 2
            pool.store_articles.assert_called_once()

    def test_skips_old_articles(self):
        now = datetime.now(timezone.utc)
        old_date = (now - timedelta(hours=48)).isoformat()
        recent_date = (now - timedelta(hours=2)).isoformat()

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url>
    <loc>https://example.se/old</loc>
    <news:news>
      <news:publication_date>{old_date}</news:publication_date>
      <news:title>Old Article</news:title>
    </news:news>
  </url>
  <url>
    <loc>https://example.se/recent</loc>
    <news:news>
      <news:publication_date>{recent_date}</news:publication_date>
      <news:title>Recent Article</news:title>
    </news:news>
  </url>
</urlset>"""
        client = _mock_client(
            {
                "https://example.se/sitemap.xml": xml,
                "https://example.se/old": "<html><body><p>Old</p></body></html>",
                "https://example.se/recent": "<html><body><p>Recent</p></body></html>",
            }
        )

        with patch("daglas.context_fetcher.httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value = client
            pool = MagicMock()
            sources = [
                {
                    "sitemap": "https://example.se/sitemap.xml",
                    "name": "test",
                    "max_age_hours": 24,
                }
            ]
            result = fetch_context(sources, pool, max_age_hours=24)
            urls = [a.url for a in result.articles]
            assert "https://example.se/recent" in urls
            assert "https://example.se/old" not in urls

    def test_sorts_by_date(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url>
    <loc>https://example.se/oldest</loc>
    <news:news>
      <news:publication_date>2026-06-01T00:00:00Z</news:publication_date>
      <news:title>Oldest</news:title>
    </news:news>
  </url>
  <url>
    <loc>https://example.se/newest</loc>
    <news:news>
      <news:publication_date>2026-06-17T00:00:00Z</news:publication_date>
      <news:title>Newest</news:title>
    </news:news>
  </url>
</urlset>"""
        client = _mock_client(
            {
                "https://example.se/sitemap.xml": xml,
                "https://example.se/oldest": "<html><body><p>Oldest</p></body></html>",
                "https://example.se/newest": "<html><body><p>Newest</p></body></html>",
            }
        )

        with patch("daglas.context_fetcher.httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value = client
            pool = MagicMock()
            result = fetch_context(
                [{"sitemap": "https://example.se/sitemap.xml", "name": "test"}], pool
            )
            assert len(result.articles) == 2
            assert result.articles[0].url == "https://example.se/newest"
            assert result.articles[1].url == "https://example.se/oldest"

    def test_sitemap_index_raises(self):
        client = _mock_client({"https://example.se/sitemap.xml": SITEMAP_INDEX_XML})

        with patch("daglas.context_fetcher.httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value = client
            pool = MagicMock()
            result = fetch_context(
                [{"sitemap": "https://example.se/sitemap.xml", "name": "test"}], pool
            )
            assert len(result.errors) >= 1
            assert "sitemap index" in result.errors[0].lower()
            pool.store_articles.assert_not_called()


class TestContextFetcherDaemon:
    def test_start_stop(self):
        pool = MagicMock()
        daemon = ContextFetcherDaemon([], pool, poll_interval=86400)
        daemon.start()
        time.sleep(0.05)
        assert daemon.is_running
        daemon.stop()
        assert not daemon.is_running

    def test_is_running_before_start(self):
        pool = MagicMock()
        daemon = ContextFetcherDaemon([], pool)
        assert not daemon.is_running

    def test_fetch_once(self):
        pool = MagicMock()
        daemon = ContextFetcherDaemon([], pool)

        with patch(
            "daglas.context_fetcher.fetch_context", return_value=FetchResult()
        ) as mock_fetch:
            result = daemon.fetch_once()
            mock_fetch.assert_called_once()
            assert isinstance(result, FetchResult)

    def test_fetch_once_with_sources(self):
        pool = MagicMock()
        sources = [{"sitemap": "https://example.se/sitemap.xml", "name": "test"}]
        daemon = ContextFetcherDaemon(sources, pool, max_articles=5)

        expected = FetchResult(articles=[Article(url="https://example.se/a")])
        with patch(
            "daglas.context_fetcher.fetch_context", return_value=expected
        ) as mock_fetch:
            result = daemon.fetch_once()
            mock_fetch.assert_called_once_with(sources, pool, max_articles=5)
            assert len(result.articles) == 1

    def test_start_stop_idempotent(self):
        pool = MagicMock()
        daemon = ContextFetcherDaemon([], pool, poll_interval=86400)
        daemon.start()
        time.sleep(0.05)
        daemon.start()
        daemon.stop()
        daemon.stop()
        assert not daemon.is_running
