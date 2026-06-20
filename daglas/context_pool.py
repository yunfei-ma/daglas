from __future__ import annotations

import json
from pathlib import Path

import daglas.config


class ContextPool:
    def __init__(self, data_dir: str | None = None):
        if data_dir:
            self._data_dir = Path(data_dir)
        elif daglas.config.config is not None:
            self._data_dir = Path(daglas.config.config.data_dir)
        else:
            self._data_dir = Path("data")

    def _path(self) -> Path:
        return self._data_dir / "context_pool.jsonl"

    def store_article(self, article: dict) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(article, ensure_ascii=False) + "\n")

    def retrieve_articles(self) -> list[dict]:
        path = self._path()
        if not path.is_file():
            return []
        articles: list[dict] = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    articles.append(json.loads(line))
        return articles

    def seen_urls(self) -> set[str]:
        path = self._path()
        if not path.is_file():
            return set()
        urls: set[str] = set()
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    if "url" in data:
                        urls.add(data["url"])
        return urls

    def clear(self) -> None:
        path = self._path()
        if path.is_file():
            path.unlink()
