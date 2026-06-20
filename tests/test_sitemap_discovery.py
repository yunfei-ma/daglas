from unittest.mock import MagicMock, patch

import httpx

from tools.discover_sitemaps import (
    COMMON_SITEMAP_PATHS,
    DiscoveryResult,
    FlatSitemapInfo,
    _score_sitemap,
    _select_best,
    discover_sitemaps,
    discover_via_robots_txt,
    fetch_and_classify,
    normalise_input,
    probe_common_paths,
    summarise_flat,
    verify_with_llm,
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
</urlset>"""

SITEMAP_INDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.se/sitemap1.xml</loc></sitemap>
  <sitemap><loc>https://example.se/sitemap2.xml</loc></sitemap>
</sitemapindex>"""

ROBOTS_TXT = """User-agent: *
Disallow: /admin
Sitemap: https://example.se/sitemap.xml
Sitemap: https://example.se/news-sitemap.xml
"""

ROBOTS_TXT_NO_SITEMAPS = """User-agent: *
Disallow: /admin
"""

ARTICLE_HTML = """<html><head><title>Swedish Article</title></head>
<body><article><h1>Swedish Article</h1>
<p>Detta ar en svensk artikel om Stockholm. Den handlar om vader och politik.</p>
<p>Det ar intressant att lasa om vad som hander i varlden.</p></article></body></html>"""


def _mock_client(responses: dict[str, str]) -> httpx.Client:
    def handler(request):
        url = str(request.url)
        if url in responses:
            return httpx.Response(200, text=responses[url])
        return httpx.Response(404)

    transport = MagicMock(spec=httpx.BaseTransport)
    transport.handle_request = handler
    return httpx.Client(transport=transport)


class TestNormaliseInput:
    def test_without_scheme(self):
        assert normalise_input("svt.se") == "https://svt.se"

    def test_with_scheme(self):
        assert normalise_input("https://svt.se") == "https://svt.se"

    def test_http_scheme(self):
        assert normalise_input("http://svt.se") == "http://svt.se"

    def test_strips_whitespace(self):
        assert normalise_input("  svt.se  ") == "https://svt.se"


class TestDiscoverViaRobotsTxt:
    def test_returns_sitemap_urls(self):
        client = _mock_client({"https://example.se/robots.txt": ROBOTS_TXT})
        urls = discover_via_robots_txt("https://example.se", client)
        assert urls == [
            "https://example.se/sitemap.xml",
            "https://example.se/news-sitemap.xml",
        ]

    def test_no_sitemaps(self):
        client = _mock_client({"https://example.se/robots.txt": ROBOTS_TXT_NO_SITEMAPS})
        urls = discover_via_robots_txt("https://example.se", client)
        assert urls == []

    def test_404_returns_empty(self):
        client = _mock_client({})
        urls = discover_via_robots_txt("https://example.se", client)
        assert urls == []


class TestProbeCommonPaths:
    def test_finds_xml_sitemaps(self):
        responses = {
            f"https://example.se{path}": SITEMAP_PLAIN_XML
            for path in COMMON_SITEMAP_PATHS[:2]
        }
        responses["https://example.se/sitemap.xml"] = SITEMAP_PLAIN_XML
        client = _mock_client(responses)
        urls = probe_common_paths("https://example.se", client)
        assert len(urls) >= 1
        assert "https://example.se/sitemap.xml" in urls

    def test_all_404_returns_empty(self):
        client = _mock_client({})
        urls = probe_common_paths("https://example.se", client)
        assert urls == []


class TestFetchAndClassify:
    def test_flat_sitemap(self):
        client = _mock_client({"https://example.se/sitemap.xml": SITEMAP_PLAIN_XML})
        result = fetch_and_classify("https://example.se/sitemap.xml", client)
        assert result == [("https://example.se/sitemap.xml", False)]

    def test_sitemap_index(self):
        client = _mock_client({"https://example.se/sitemap.xml": SITEMAP_INDEX_XML})
        result = fetch_and_classify("https://example.se/sitemap.xml", client)
        assert ("https://example.se/sitemap1.xml", True) in result
        assert ("https://example.se/sitemap2.xml", True) in result
        assert len(result) == 2


class TestSummariseFlat:
    def test_with_news_metadata(self):
        client = _mock_client({"https://example.se/sitemap.xml": SITEMAP_NEWS_XML})
        info = summarise_flat("https://example.se/sitemap.xml", client)
        assert info.entry_count == 2
        assert info.has_news_namespace is True
        assert info.sample_titles == ["Article A Title"]

    def test_plain_sitemap(self):
        client = _mock_client({"https://example.se/sitemap.xml": SITEMAP_PLAIN_XML})
        info = summarise_flat("https://example.se/sitemap.xml", client)
        assert info.entry_count == 2
        assert info.has_news_namespace is False
        assert info.sample_titles == []


class TestVerifyWithLlm:
    SAMPLE_COUNT = 1

    def test_llm_says_yes(self):
        responses = {
            "https://example.se/sitemap.xml": SITEMAP_PLAIN_XML,
            "https://example.se/artikel/1": ARTICLE_HTML,
        }
        client = _mock_client(responses)
        mock_llm = MagicMock()
        mock_llm.prompt.return_value = "yes: Swedish article, accessible"
        verdicts, passed, total = verify_with_llm(
            "https://example.se/sitemap.xml",
            client,
            mock_llm,
            sample_count=self.SAMPLE_COUNT,
        )
        assert len(verdicts) == 1
        assert passed == 1
        assert total == 1
        assert verdicts[0].suitable is True
        assert verdicts[0].reason

    def test_llm_says_no(self):
        responses = {
            "https://example.se/sitemap.xml": SITEMAP_PLAIN_XML,
            "https://example.se/artikel/1": ARTICLE_HTML,
        }
        client = _mock_client(responses)
        mock_llm = MagicMock()
        mock_llm.prompt.return_value = "no: paywalled content"
        verdicts, passed, total = verify_with_llm(
            "https://example.se/sitemap.xml",
            client,
            mock_llm,
            sample_count=self.SAMPLE_COUNT,
        )
        assert len(verdicts) == 1
        assert passed == 0
        assert verdicts[0].suitable is False
        assert "paywalled" in verdicts[0].reason

    def test_paywall_precheck(self):
        paywall_html = """<html><body><p>Logga in for att lasa mer. Prenumerera for full tillgang.</p></body></html>"""
        responses = {
            "https://example.se/sitemap.xml": SITEMAP_PLAIN_XML,
            "https://example.se/artikel/1": paywall_html,
        }
        client = _mock_client(responses)
        mock_llm = MagicMock()
        verdicts, passed, total = verify_with_llm(
            "https://example.se/sitemap.xml",
            client,
            mock_llm,
            sample_count=self.SAMPLE_COUNT,
        )
        assert len(verdicts) == 1
        assert passed == 0
        assert verdicts[0].suitable is False
        assert "paywall" in verdicts[0].reason
        mock_llm.prompt.assert_not_called()

    def test_article_fetch_failure(self):
        responses = {
            "https://example.se/sitemap.xml": SITEMAP_PLAIN_XML,
        }
        client = _mock_client(responses)
        mock_llm = MagicMock()
        verdicts, passed, total = verify_with_llm(
            "https://example.se/sitemap.xml",
            client,
            mock_llm,
            sample_count=self.SAMPLE_COUNT,
        )
        assert len(verdicts) == 1
        assert passed == 0
        assert verdicts[0].suitable is False
        assert "fetch failed" in verdicts[0].reason


class TestScoreSitemap:
    def test_news_sitemap_beats_plain(self):
        news = FlatSitemapInfo(
            url="https://example.se/news-sitemap.xml",
            entry_count=50,
            date_range=("2026-06-15", "2026-06-17"),
            has_news_namespace=True,
        )
        plain = FlatSitemapInfo(
            url="https://example.se/sitemap.xml",
            entry_count=100,
            date_range=("2026-06-10", "2026-06-16"),
            has_news_namespace=False,
        )
        assert _score_sitemap(news) > _score_sitemap(plain)

    def test_fresh_small_beats_archive(self):
        fresh = FlatSitemapInfo(
            url="https://example.se/sitemap.xml",
            entry_count=100,
            date_range=("2026-06-16", "2026-06-17"),
            has_news_namespace=False,
        )
        archive = FlatSitemapInfo(
            url="https://example.se/sitemap.xml",
            entry_count=50000,
            date_range=("2020-01-01", "2026-06-17"),
            has_news_namespace=False,
        )
        assert _score_sitemap(fresh) > _score_sitemap(archive)

    def test_select_best_picks_news_over_archive(self):
        news = FlatSitemapInfo(
            url="https://example.se/news-sitemap.xml",
            entry_count=200,
            date_range=("2026-06-16", "2026-06-17"),
            has_news_namespace=True,
        )
        archive = FlatSitemapInfo(
            url="https://example.se/archive-sitemap.xml",
            entry_count=99999,
            date_range=("2010-01-01", "2026-06-17"),
            has_news_namespace=False,
        )
        best = _select_best([archive, news])
        assert best is not None
        assert best.has_news_namespace is True

    def test_select_best_empty(self):
        assert _select_best([]) is None

    def test_url_keyword_deprioritized(self):
        archive_url = FlatSitemapInfo(
            url="https://example.se/sitemap-page-3.xml",
            entry_count=100,
            date_range=("2026-06-10", "2026-06-17"),
            has_news_namespace=False,
        )
        clean = FlatSitemapInfo(
            url="https://example.se/sitemap-latest.xml",
            entry_count=100,
            date_range=("2026-06-10", "2026-06-17"),
            has_news_namespace=False,
        )
        assert _score_sitemap(clean) > _score_sitemap(archive_url)


class TestFullPipeline:
    def _make_client(self, responses: dict[str, str]) -> httpx.Client:
        transport = MagicMock(spec=httpx.BaseTransport)

        def handler(req):
            url = str(req.url)
            if url in responses:
                return httpx.Response(200, text=responses[url])
            return httpx.Response(404)

        transport.handle_request = handler
        return httpx.Client(transport=transport)

    def test_basic_discovery(self):
        responses = {
            "https://example.se/robots.txt": ROBOTS_TXT,
            "https://example.se/sitemap.xml": SITEMAP_PLAIN_XML,
            "https://example.se/news-sitemap.xml": SITEMAP_PLAIN_XML,
        }
        client = self._make_client(responses)
        result = discover_sitemaps("example.se", verify=False, client=client)
        assert isinstance(result, DiscoveryResult)
        all_flat = result.found_via_robots + result.found_via_probing
        assert len(all_flat) >= 1
        assert all_flat[0].entry_count > 0

    def test_no_sitemaps_found(self):
        client = self._make_client({})
        result = discover_sitemaps("invalid.example", verify=False, client=client)
        all_flat = result.found_via_robots + result.found_via_probing
        assert len(all_flat) == 0

    def test_index_recursion(self):
        responses = {
            "https://example.se/robots.txt": ROBOTS_TXT,
            "https://example.se/sitemap.xml": SITEMAP_INDEX_XML,
            "https://example.se/sitemap1.xml": SITEMAP_PLAIN_XML,
            "https://example.se/sitemap2.xml": SITEMAP_PLAIN_XML,
        }
        client = self._make_client(responses)
        result = discover_sitemaps("example.se", verify=False, client=client)
        all_flat = result.found_via_robots + result.found_via_probing
        assert len(all_flat) == 2
        for info in all_flat:
            assert info.entry_count > 0

    def test_verify_in_pipeline(self):
        responses = {
            "https://example.se/robots.txt": ROBOTS_TXT,
            "https://example.se/sitemap.xml": SITEMAP_PLAIN_XML,
            "https://example.se/news-sitemap.xml": SITEMAP_PLAIN_XML,
            "https://example.se/artikel/1": ARTICLE_HTML,
            "https://example.se/artikel/2": ARTICLE_HTML,
        }
        mock_llm = MagicMock()
        mock_llm.prompt.return_value = "yes: good Swedish article"

        client = self._make_client(responses)
        result = discover_sitemaps(
            "example.se",
            verify=True,
            llm_provider=mock_llm,
            client=client,
        )
        all_flat = result.found_via_robots + result.found_via_probing
        assert len(all_flat) >= 1
        best = _select_best(all_flat)
        assert best is not None
        assert best.llm_verdict is not None
        assert best.llm_verdict.suitable is True
        assert best.verify_passed >= 1
        assert best.verify_total >= 1


class TestEdgeCases:
    def test_cycle_detection(self):
        cycle_xml = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.se/sitemap.xml</loc></sitemap>
</sitemapindex>"""
        responses = {
            "https://example.se/robots.txt": "Sitemap: https://example.se/sitemap.xml\n",
            "https://example.se/sitemap.xml": cycle_xml,
        }
        with patch("tools.discover_sitemaps.httpx.Client") as mock_client_cls:
            transport = MagicMock(spec=httpx.BaseTransport)
            transport.handle_request = lambda req: (
                httpx.Response(200, text=responses.get(str(req.url), "")),
            )
            mock_client_cls.return_value = httpx.Client(transport=transport)

            result = discover_sitemaps("example.se", verify=False)
            assert isinstance(result, DiscoveryResult)

    def test_max_depth(self):
        deep_index = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.se/deep1.xml</loc></sitemap>
</sitemapindex>"""
        responses = {
            "https://example.se/robots.txt": "Sitemap: https://example.se/sitemap.xml\n",
            "https://example.se/sitemap.xml": deep_index,
            "https://example.se/deep1.xml": deep_index,
        }
        with patch("tools.discover_sitemaps.httpx.Client") as mock_client_cls:
            transport = MagicMock(spec=httpx.BaseTransport)
            transport.handle_request = lambda req: (
                httpx.Response(200, text=responses.get(str(req.url), "")),
            )
            mock_client_cls.return_value = httpx.Client(transport=transport)

            result = discover_sitemaps("example.se", max_depth=1)
            all_flat = result.found_via_robots + result.found_via_probing
            assert len(all_flat) == 0
