from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable

import httpx

from daglas.lesson.llm_mlx import MlxModel, ModelState  # noqa: F401

logger = logging.getLogger(__name__)

VALID_BACKENDS = frozenset({"ollama", "llamacpp", "mlx_local"})

BACKEND_DEFAULTS: dict[str, dict] = {
    "ollama": {
        "endpoint": "http://localhost:11434/v1",
        "model": "llama3.2",
    },
    "llamacpp": {
        "endpoint": "http://localhost:8080/v1",
        "model": "",
    },
    "mlx_local": {
        "model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
    },
}

_BACKEND_ALIASES = {"mlx": "mlx_local"}


class Llm:
    """Prompt queue with HTTP dispatch to an external LLM server.

    For ollama/llamacpp backends: server assumed to be running externally.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        model: str = "",
        api_key: str = "",
        max_tokens: int = 2048,
        idle_timeout: float = 20.0,
    ):
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._idle_timeout = idle_timeout

        self._queue: queue.Queue = queue.Queue()
        self._pending: dict[str, Callable[[str | None], None]] = {}
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def prompt(self, system: str = "", user: str = "") -> str | None:
        return self.prompt_sync(system=system, prompt=user)

    def post(
        self,
        prompt: str,
        system: str = "",
        callback: Callable[[str | None], None] | None = None,
    ) -> None:
        item_id = str(uuid.uuid4())
        if callback:
            self._pending[item_id] = callback
        self._queue.put({"id": item_id, "system": system, "prompt": prompt})
        self._ensure_worker()

    def prompt_sync(self, system: str = "", prompt: str = "") -> str | None:
        result: list[str | None] = []
        event = threading.Event()

        def done(text: str | None) -> None:
            result.append(text)
            event.set()

        self.post(prompt, system=system, callback=done)
        event.wait()
        return result[0]

    def close(self) -> None:
        self._stop_event.set()
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._thread.start()

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=self._idle_timeout)
            except queue.Empty:
                break

            if item is None:
                break

            try:
                text = self._call_llm(item.get("system", ""), item["prompt"])
                self._dispatch(item["id"], text)
            except Exception:
                logger.exception("LLM call failed")
                self._dispatch(item["id"], None)

    def _call_llm(self, system: str, user: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        body: dict = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "max_tokens": self._max_tokens,
        }
        if self._model:
            body["model"] = self._model

        url = f"{self._endpoint}/chat/completions"
        for attempt in range(30):
            try:
                resp = httpx.post(url, headers=headers, json=body, timeout=300)
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
            except (httpx.ConnectError, httpx.RemoteProtocolError):
                if attempt < 29:
                    time.sleep(2)
                    continue
                raise

    def _dispatch(self, item_id: str, text: str | None) -> None:
        callback = self._pending.pop(item_id, None)
        if callback:
            callback(text)


def create_llm(cfg) -> Llm | MlxModel:
    """Build a configured Llm or MlxModel from DaglasConfig."""
    backend = (cfg.llm_backend or "").lower()
    backend = _BACKEND_ALIASES.get(backend, backend)

    if backend not in VALID_BACKENDS:
        raise ValueError(
            f"Unknown llm_backend={cfg.llm_backend!r}. "
            f"Valid: {', '.join(sorted(VALID_BACKENDS))}"
        )

    defaults = BACKEND_DEFAULTS[backend]

    if backend == "mlx_local":
        model = cfg.llm_model or defaults["model"]
        return MlxModel(
            model=model,
            max_tokens=getattr(cfg, "llm_max_tokens", 2048),
            idle_timeout=getattr(cfg, "llm_idle_timeout", 20.0),
            hf_cache_dir=getattr(cfg, "hf_cache_dir", ""),
            enable_thinking=bool(getattr(cfg, "llm_enable_thinking", False)),
        )

    endpoint = cfg.llm_endpoint or defaults["endpoint"]
    model = cfg.llm_model or defaults["model"]

    return Llm(
        endpoint=endpoint,
        model=model,
        api_key=cfg.llm_api_key or "",
        max_tokens=getattr(cfg, "llm_max_tokens", 2048),
        idle_timeout=getattr(cfg, "llm_idle_timeout", 20.0),
    )
