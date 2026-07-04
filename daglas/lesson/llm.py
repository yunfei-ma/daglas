from __future__ import annotations

import logging
import queue
import subprocess
import threading
import time
import uuid
from collections.abc import Callable

import httpx

logger = logging.getLogger(__name__)

VALID_BACKENDS = frozenset({"ollama", "llamacpp", "mlx_server"})

BACKEND_DEFAULTS: dict[str, dict] = {
    "ollama": {
        "endpoint": "http://localhost:11434/v1",
        "model": "llama3.2",
    },
    "llamacpp": {
        "endpoint": "http://localhost:8080/v1",
        "model": "",
    },
    "mlx_server": {
        "endpoint": "http://localhost:8081/v1",
        "model": "mlx-community/Llama-3.2-3B-Instruct-4bit",
        "server_cmd": [
            "mlx_lm.server",
            "--model",
            "mlx-community/Llama-3.2-3B-Instruct-4bit",
            "--host",
            "127.0.0.1",
            "--port",
            "8081",
        ],
    },
}


class Llm:
    """Prompt queue with HTTP dispatch and optional server lifecycle.

    For mlx_server backend: manages mlx_lm.server subprocess — spawn
    before worker loop, SIGTERM after loop.

    For ollama/llamacpp backends: no process management; server
    assumed to be running externally. Start/stop are no-ops.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        model: str = "",
        api_key: str = "",
        max_tokens: int = 2048,
        manage_process: bool = False,
        server_cmd: list[str] | None = None,
        idle_timeout: float = 20.0,
    ):
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._manage_process = manage_process
        self._server_cmd = server_cmd or []
        self._idle_timeout = idle_timeout

        self._queue: queue.Queue = queue.Queue()
        self._pending: dict[str, Callable[[str], None]] = {}
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def prompt(self, system: str = "", user: str = "") -> str:
        return self.prompt_sync(system=system, prompt=user)

    def post(
        self,
        prompt: str,
        system: str = "",
        callback: Callable[[str], None] | None = None,
    ) -> None:
        """Enqueue a prompt. Returns immediately."""
        item_id = str(uuid.uuid4())
        if callback:
            self._pending[item_id] = callback
        self._queue.put({"id": item_id, "system": system, "prompt": prompt})
        self._ensure_worker()

    def prompt_sync(self, system: str = "", prompt: str = "") -> str:
        """Synchronous wrapper around post()."""
        result: list[str] = []
        event = threading.Event()

        def done(text: str) -> None:
            result.append(text)
            event.set()

        self.post(prompt, system=system, callback=done)
        event.wait()
        return result[0]

    def close(self) -> None:
        """Stop worker thread and clean up server process."""
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

    def _start_server(self) -> None:
        if not self._manage_process or not self._server_cmd:
            return
        self._process = subprocess.Popen(self._server_cmd)
        logger.info("LLM server started pid=%d", self._process.pid)
        for _ in range(60):
            if self._stop_event.is_set():
                return
            try:
                resp = httpx.get(f"{self._endpoint}/models", timeout=1)
                if resp.status_code == 200:
                    logger.info("LLM server ready")
                    return
            except Exception:
                pass
            time.sleep(1)
        logger.warning("LLM server did not become ready within 60 seconds")

    def _stop_server(self) -> None:
        if not self._manage_process:
            return
        with self._lock:
            if self._process is None:
                return
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                self._process.kill()
            self._process = None
            logger.info("LLM server terminated")

    def _worker_loop(self) -> None:
        self._start_server()
        try:
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
                except Exception as exc:
                    logger.exception("LLM call failed")
                    self._dispatch(item["id"], str(exc))
        finally:
            self._stop_server()

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

    def _dispatch(self, item_id: str, text: str) -> None:
        callback = self._pending.pop(item_id, None)
        if callback:
            callback(text)


_BACKEND_ALIASES = {"mlx": "mlx_server"}


def create_llm(cfg) -> Llm:
    """Build a configured Llm instance from DaglasConfig."""
    backend = (cfg.llm_backend or "").lower()
    backend = _BACKEND_ALIASES.get(backend, backend)

    if backend not in VALID_BACKENDS:
        raise ValueError(
            f"Unknown llm_backend={cfg.llm_backend!r}. "
            f"Valid: {', '.join(sorted(VALID_BACKENDS))}"
        )

    defaults = BACKEND_DEFAULTS[backend]
    endpoint = cfg.llm_endpoint or defaults["endpoint"]
    model = cfg.llm_model or defaults["model"]
    server_cmd = defaults.get("server_cmd", [])

    return Llm(
        endpoint=endpoint,
        model=model,
        api_key=cfg.llm_api_key or "",
        max_tokens=getattr(cfg, "llm_max_tokens", 2048),
        manage_process=backend == "mlx_server",
        server_cmd=server_cmd,
        idle_timeout=getattr(cfg, "llm_idle_timeout", 20.0),
    )
