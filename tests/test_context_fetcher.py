import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx

from daglas.context_fetcher import (
    Article,
    ContextFetcherDaemon,
    SiteThreadContext,
    SitemapEntry,
    _backfill_article_date,
    _parse_date,
    deduplicate,
    extract_article,
    fetch_context,
    read_sitemap_entries,
    _scan_html_for_date,
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


class TestScanHtmlForDate:
    def test_meta_published_time(self):
        html = '<html><head><meta property="article:published_time" content="2026-06-20T10:00:00+02:00" /></head><body></body></html>'
        assert _scan_html_for_date(html) == "2026-06-20T10:00:00+02:00"

    def test_meta_name_published_time(self):
        html = '<html><head><meta name="article:published_time" content="2026-06-19T08:00:00Z" /></head><body></body></html>'
        assert _scan_html_for_date(html) == "2026-06-19T08:00:00Z"

    def test_json_ld_date_published(self):
        html = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","datePublished":"2026-06-18T12:00:00+02:00"}
</script></head><body></body></html>"""
        assert _scan_html_for_date(html) == "2026-06-18T12:00:00+02:00"

    def test_json_ld_date_modified_fallback(self):
        html = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","dateModified":"2026-06-17T14:00:00Z"}
</script></head><body></body></html>"""
        assert _scan_html_for_date(html) == "2026-06-17T14:00:00Z"

    def test_time_datetime(self):
        html = '<html><body><article><time datetime="2026-06-16T09:00:00+02:00">June 16</time></article></body></html>'
        assert _scan_html_for_date(html) == "2026-06-16T09:00:00+02:00"

    def test_meta_modified_time(self):
        html = '<html><head><meta property="article:modified_time" content="2026-06-15T11:00:00+02:00" /></head><body></body></html>'
        assert _scan_html_for_date(html) == "2026-06-15T11:00:00+02:00"

    def test_no_date(self):
        html = "<html><body><p>No date here</p></body></html>"
        assert _scan_html_for_date(html) is None

    def test_priority_published_over_modified(self):
        html = """<html><head>
<meta property="article:published_time" content="2026-06-20T10:00:00+02:00" />
<meta property="article:modified_time" content="2026-06-21T10:00:00+02:00" />
</head><body></body></html>"""
        assert _scan_html_for_date(html) == "2026-06-20T10:00:00+02:00"

    def test_priority_meta_over_jsonld(self):
        html = """<html><head>
<meta property="article:published_time" content="2026-06-19T08:00:00Z" />
<script type="application/ld+json">{"datePublished": "2026-06-18T00:00:00Z"}</script>
</head><body></body></html>"""
        assert _scan_html_for_date(html) == "2026-06-19T08:00:00Z"

    def test_empty_html(self):
        assert _scan_html_for_date("") is None

    def test_invalid_json_ld_skipped(self):
        html = """<html><head>
<script type="application/ld+json">{invalid}</script>
<meta property="article:published_time" content="2026-06-20T10:00:00+02:00" />
</head><body></body></html>"""
        assert _scan_html_for_date(html) == "2026-06-20T10:00:00+02:00"

    def test_extract_article_fallback(self):
        html = """<html><head>
<meta property="article:published_time" content="2026-06-20T10:00:00+02:00" />
<title>Test</title></head>
<body><article><p>Content</p></article></body></html>"""
        article = extract_article("https://example.se/a", html)
        # trafilatura now extracts a date-first (just the date part),
        # which takes priority over the meta-tag fallback
        assert article.publish_date == "2026-06-20"

    def test_extract_article_fallback_no_text_body(self):
        html = """<html><head>
<meta property="article:published_time" content="2026-06-19T08:00:00Z" />
<title>Fallback</title></head>
<body><main><p>Fallback body</p></main></body></html>"""
        article = extract_article("https://example.se/b", html)
        # trafilatura date (just the date part) takes priority over meta-tag fallback
        assert article.publish_date == "2026-06-19"


class TestParseDate:
    def test_z_suffix_normalised(self):
        dt = _parse_date("2026-06-20T10:00:00Z")
        assert dt.tzinfo is not None
        assert dt.isoformat().endswith("+00:00")

    def test_with_offset(self):
        dt = _parse_date("2026-06-20T10:00:00+02:00")
        assert dt.tzinfo is not None

    def test_naive_becomes_utc(self):
        dt = _parse_date("2026-06-20T10:00:00")
        assert dt.tzinfo is not None
        assert str(dt.tzinfo) == "UTC"


class TestBackfillArticleDate:
    def test_backfill_from_entry(self):
        article = Article(url="https://example.se/a", body="x")
        entry = SitemapEntry(url="https://example.se/a", publish_date="2026-06-20")
        _backfill_article_date(article, entry)
        assert article.publish_date == "2026-06-20"

    def test_keep_existing_date(self):
        article = Article(
            url="https://example.se/a", body="x", publish_date="2026-06-19"
        )
        entry = SitemapEntry(url="https://example.se/a", publish_date="2026-06-20")
        _backfill_article_date(article, entry)
        assert article.publish_date == "2026-06-19"

    def test_no_date_either_side(self):
        article = Article(url="https://example.se/a", body="x")
        entry = SitemapEntry(url="https://example.se/a")
        _backfill_article_date(article, entry)
        assert article.publish_date is None


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
        pool.store_article.assert_not_called()

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
            pool.store_article.assert_not_called()

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
            pool.store_article.assert_not_called()

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
            assert pool.store_article.call_count == 2  # one call per unique article

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

    def test_max_daily_articles_per_source(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url><loc>https://example.se/1</loc><news:news><news:publication_date>2026-06-20T00:00:00Z</news:publication_date></news:news></url>
  <url><loc>https://example.se/2</loc><news:news><news:publication_date>2026-06-20T00:00:00Z</news:publication_date></news:news></url>
  <url><loc>https://example.se/3</loc><news:news><news:publication_date>2026-06-20T00:00:00Z</news:publication_date></news:news></url>
</urlset>"""
        body_html = "<html><body><p>Content</p></body></html>"
        responses = {
            "https://example.se/sitemap.xml": xml,
            "https://example.se/1": body_html,
            "https://example.se/2": body_html,
            "https://example.se/3": body_html,
        }
        client = _mock_client(responses)
        with patch("daglas.context_fetcher.httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value = client
            pool = MagicMock()
            result = fetch_context(
                [
                    {
                        "sitemap": "https://example.se/sitemap.xml",
                        "name": "test",
                        "max_daily_articles": 2,
                    }
                ],
                pool,
            )
            assert len(result.articles) == 2

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
            pool.store_article.assert_not_called()


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

    def test_fetch_once_empty_sources(self):
        pool = MagicMock()
        daemon = ContextFetcherDaemon([], pool)
        daemon.fetch_once()
        assert daemon._contexts == {}

    def test_fetch_once_with_sources(self):
        pool = MagicMock()
        sources = [{"sitemap": "https://example.se/sitemap.xml", "name": "test"}]
        daemon = ContextFetcherDaemon(sources, pool, max_site_threads=4)

        with (
            patch(
                "daglas.context_fetcher.read_sitemap_entries",
                return_value=[],
            ),
        ):
            daemon.fetch_once()
            assert "test" in daemon._contexts
            assert daemon._contexts["test"].status == "COMPLETED"

    def test_start_stop_idempotent(self):
        pool = MagicMock()
        daemon = ContextFetcherDaemon([], pool, poll_interval=86400)
        daemon.start()
        time.sleep(0.05)
        daemon.start()
        daemon.stop()
        daemon.stop()
        assert not daemon.is_running

    def test_stop_context_unknown_is_noop(self):
        pool = MagicMock()
        daemon = ContextFetcherDaemon([], pool)
        daemon.stop_context("nonexistent")
        assert True  # no error

    def test_stop_context_signals_context(self):
        pool = MagicMock()
        sources = [{"sitemap": "https://example.se/sitemap.xml", "name": "test"}]
        daemon = ContextFetcherDaemon(sources, pool, max_site_threads=4)

        daemon.fetch_once()
        ctx = daemon._contexts["test"]
        assert not ctx._stop_event.is_set()

        daemon.stop_context("test")
        assert ctx._stop_event.is_set()

    def test_status_unknown_source(self):
        pool = MagicMock()
        daemon = ContextFetcherDaemon([], pool)
        result = daemon.status("nonexistent")
        assert result == {"error": "source not found"}

    def test_status_single_source(self):
        pool = MagicMock()
        sources = [{"sitemap": "https://example.se/sitemap.xml", "name": "test"}]
        daemon = ContextFetcherDaemon(sources, pool, max_site_threads=4)

        with patch("daglas.context_fetcher.read_sitemap_entries", return_value=[]):
            daemon.fetch_once()
            result = daemon.status("test")
            assert result["status"] in ("COMPLETED", "RUNNING")
            assert "articles_count" in result
            assert "errors" in result

    def test_status_all_sources(self):
        pool = MagicMock()
        sources = [
            {"sitemap": "https://a.se/sitemap.xml", "name": "a"},
            {"sitemap": "https://b.se/sitemap.xml", "name": "b"},
        ]
        daemon = ContextFetcherDaemon(sources, pool, max_site_threads=4)

        with patch("daglas.context_fetcher.read_sitemap_entries", return_value=[]):
            daemon.fetch_once()
            result = daemon.status()
            assert "a" in result
            assert "b" in result
            for info in result.values():
                assert "status" in info
                assert "articles_count" in info
                assert "errors" in info


class TestSiteThreadContext:
    def test_initial_state(self):
        ctx = SiteThreadContext(source_config={}, store=MagicMock())
        assert ctx.status == "PENDING"
        assert ctx.errors == []
        assert ctx.seen == set()
        assert not ctx._stop_event.is_set()

    def test_stop_sets_event(self):
        ctx = SiteThreadContext(source_config={}, store=MagicMock())
        ctx.stop()
        assert ctx._stop_event.is_set()

    def test_run_sets_completed_with_empty_sitemap(self):
        pool = MagicMock()
        ctx = SiteThreadContext(
            source_config={"sitemap": "https://example.se/sitemap.xml", "name": "test"},
            store=pool,
        )
        with patch("daglas.context_fetcher.read_sitemap_entries", return_value=[]):
            ctx.run()
            assert ctx.status == "COMPLETED"

    def test_run_sets_failed_on_sitemap_error(self):
        pool = MagicMock()
        ctx = SiteThreadContext(
            source_config={"sitemap": "https://example.se/sitemap.xml", "name": "test"},
            store=pool,
        )
        with patch(
            "daglas.context_fetcher.read_sitemap_entries",
            side_effect=httpx.ConnectError("fail"),
        ):
            ctx.run()
            assert ctx.status == "FAILED"
            pool.store_article.assert_not_called()

    def test_run_stops_on_event(self):
        pool = MagicMock()
        ctx = SiteThreadContext(
            source_config={"sitemap": "https://example.se/sitemap.xml", "name": "test"},
            store=pool,
        )
        ctx.stop()
        with patch("daglas.context_fetcher.read_sitemap_entries", return_value=[]):
            ctx.run()
            assert ctx.status == "STOPPED"

    def test_run_fetches_real_articles(self):
        pool = MagicMock()
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url>
    <loc>https://example.se/a</loc>
    <news:news><news:publication_date>2026-06-20T00:00:00Z</news:publication_date><news:title>Article A</news:title></news:news>
  </url>
  <url>
    <loc>https://example.se/b</loc>
    <news:news><news:publication_date>2026-06-19T00:00:00Z</news:publication_date></news:news>
  </url>
</urlset>"""
        article_html = """<html><head><meta property="article:published_time" content="2026-06-20T10:00:00+02:00" /><title>Article A</title></head><body><article><p>Content</p></article></body></html>"""
        responses = {
            "https://example.se/sitemap.xml": xml,
            "https://example.se/a": article_html,
            "https://example.se/b": "<html><body><main><p>Other</p></main></body></html>",
        }
        client = _mock_client(responses)

        with (
            patch("daglas.context_fetcher.httpx.Client") as mock_client_cls,
            patch.object(SiteThreadContext, "_load_seen_urls"),
        ):
            mock_client_cls.return_value.__enter__.return_value = client
            ctx = SiteThreadContext(
                source_config={
                    "sitemap": "https://example.se/sitemap.xml",
                    "name": "test",
                },
                store=pool,
            )
            ctx.run()
            assert ctx.status == "COMPLETED"
            assert pool.store_article.call_count == 2
            stored_urls = [
                call[0][0]["url"] for call in pool.store_article.call_args_list
            ]
            assert "https://example.se/a" in stored_urls
            assert "https://example.se/b" in stored_urls
