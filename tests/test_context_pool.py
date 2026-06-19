from pathlib import Path

from daglas.context_pool import ContextPool


class TestContextPool:
    def test_store_and_retrieve(self, tmp_path: Path):
        pool = ContextPool(data_dir=str(tmp_path))
        articles = [
            {"url": "https://a.se/1", "title": "A", "body": "Content A"},
            {"url": "https://a.se/2", "title": "B", "body": "Content B"},
        ]
        for article in articles:
            pool.store_article(article)
        retrieved = pool.retrieve_articles()
        assert len(retrieved) == 2
        assert retrieved[0]["title"] == "A"
        assert retrieved[1]["title"] == "B"

    def test_retrieve_empty(self, tmp_path: Path):
        pool = ContextPool(data_dir=str(tmp_path))
        assert pool.retrieve_articles() == []

    def test_clear(self, tmp_path: Path):
        pool = ContextPool(data_dir=str(tmp_path))
        pool.store_article({"url": "https://a.se/1", "title": "A", "body": ""})
        assert len(pool.retrieve_articles()) == 1
        pool.clear()
        assert pool.retrieve_articles() == []

    def test_append_to_existing(self, tmp_path: Path):
        pool = ContextPool(data_dir=str(tmp_path))
        pool.store_article({"url": "https://a.se/1", "title": "A", "body": ""})
        pool.store_article({"url": "https://a.se/2", "title": "B", "body": ""})
        assert len(pool.retrieve_articles()) == 2
