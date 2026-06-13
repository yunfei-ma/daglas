from unittest.mock import MagicMock, patch

import httpx

from daglas.context_fetcher import (
    Article,
    deduplicate,
    discover_sitemap_urls,
    extract_article,
    fetch_context,
)

SITEMAP_FLAT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.se/artikel/1</loc></url>
  <url><loc>https://example.se/artikel/2</loc></url>
</urlset>"""

SITEMAP_INDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.se/sitemap1.xml</loc></sitemap>
  <sitemap><loc>https://example.se/sitemap2.xml</loc></sitemap>
</sitemapindex>"""

CHILD_SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.se/artikel/a</loc></url>
</urlset>"""

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


class TestDiscoverSitemapUrls:
    def test_discover_sitemap_flat(self):
        client = _mock_client({"https://example.se/sitemap.xml": SITEMAP_FLAT_XML})
        urls = discover_sitemap_urls("https://example.se/sitemap.xml", client)
        assert urls == {"https://example.se/artikel/1", "https://example.se/artikel/2"}

    def test_discover_sitemap_index(self):
        responses = {
            "https://example.se/sitemap.xml": SITEMAP_INDEX_XML,
            "https://example.se/sitemap1.xml": CHILD_SITEMAP_XML,
            "https://example.se/sitemap2.xml": CHILD_SITEMAP_XML,
        }
        client = _mock_client(responses)
        urls = discover_sitemap_urls("https://example.se/sitemap.xml", client)
        assert urls == {"https://example.se/artikel/a"}

    def test_discover_sitemap_empty(self):
        client = _mock_client({"https://example.se/sitemap.xml": SITEMAP_EMPTY_XML})
        urls = discover_sitemap_urls("https://example.se/sitemap.xml", client)
        assert urls == set()


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
    def test_fetch_context_no_sources(self):
        pool = MagicMock()
        result = fetch_context([], pool)
        assert result.articles == []
        assert result.errors == []
        pool.store_articles.assert_not_called()

    def test_fetch_context_sitemap_unreachable(self):
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

    def test_fetch_context_all_fail(self):
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

    def test_fetch_context_deduplicates_across_sitemaps(self):
        ARTICLE_HTML_SIMPLE = "<html><body><p>Hej</p></body></html>"
        responses = {
            "https://a.se/sitemap.xml": SITEMAP_FLAT_XML,
            "https://b.se/sitemap.xml": SITEMAP_FLAT_XML,
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
