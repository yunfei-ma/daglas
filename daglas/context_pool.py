from __future__ import annotations

import json
from datetime import date
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

    def _today_path(self) -> Path:
        return self._data_dir / f"{date.today().isoformat()}.jsonl"

    def store_article(self, article: dict) -> None:
        """Append one article to today's jsonl file, creating dirs as needed."""
        path = self._today_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(article, ensure_ascii=False) + "\n")

    def retrieve_articles(self) -> list[dict]:
        path = self._today_path()
        if not path.is_file():
            return []
        articles: list[dict] = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    articles.append(json.loads(line))
        return articles

    def clear(self) -> None:
        path = self._today_path()
        if path.is_file():
            path.unlink()
