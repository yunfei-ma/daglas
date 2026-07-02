from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class LlmProvider(Protocol):
    def prompt(self, system: str, user: str) -> str: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...


# ---------------------------------------------------------------------------
# HTTP providers — run inside the LLM subprocess
# ---------------------------------------------------------------------------


class LlmOllama:
    def __init__(
        self,
        endpoint: str = "http://localhost:11434/v1",
        model: str = "llama3.2",
        api_key: str = "",
    ):
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._api_key = api_key

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def prompt(self, system: str, user: str) -> str:
        import httpx

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        resp = httpx.post(
            f"{self._endpoint}/chat/completions",
            headers=headers,
            json=body,
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


class LlmLlamaCpp:
    def __init__(
        self,
        endpoint: str = "http://localhost:8080/v1",
        model: str = "",
        api_key: str = "",
    ):
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._api_key = api_key

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def prompt(self, system: str, user: str) -> str:
        import httpx

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": 2048,
        }
        resp = httpx.post(
            f"{self._endpoint}/chat/completions",
            headers=headers,
            json=body,
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Llm — public API (main process)
# ---------------------------------------------------------------------------


class Llm:
    """Public API, runs in main process (no LLM model loaded).
    post() writes a JSON line to prompts.jsonl and returns.
    If the LLM subprocess is not running, _ensure_process() spawns it
    via the main() entry point.  A response thread polls responses.jsonl
    and dispatches results to callbacks."""

    def __init__(self, data_dir: str = "data"):
        self._data_dir = Path(data_dir)
        self._prompts_path = self._data_dir / "llm" / "prompts.jsonl"
        self._responses_path = self._data_dir / "llm" / "responses.jsonl"
        self._prompts_path.parent.mkdir(parents=True, exist_ok=True)

        self._pending: dict[str, Callable[[str], None]] = {}
        self._process: subprocess.Popen | None = None
        self._resp_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def prompt(self, system: str = "", user: str = "") -> str:
        """Synchronous convenience — satisfies LlmProvider protocol."""
        return self.prompt_sync(system=system, prompt=user)

    def post(
        self,
        prompt: str,
        system: str = "",
        callback: Callable[[str], None] | None = None,
    ) -> None:
        """Enqueue a prompt via prompts.jsonl. Returns immediately."""
        item_id = str(uuid.uuid4())
        if callback:
            self._pending[item_id] = callback
        line = json.dumps(
            {
                "id": item_id,
                "system": system,
                "prompt": prompt,
                "queued_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        )
        with self._lock:
            with open(self._prompts_path, "a") as f:
                f.write(line + "\n")
        self._ensure_process()

    def prompt_sync(self, system: str = "", prompt: str = "") -> str:
        """Synchronous convenience wrapper around post()."""
        result: list[str] = []
        event = threading.Event()

        def done(text: str) -> None:
            result.append(text)
            event.set()

        self.post(prompt, system=system, callback=done)
        event.wait()
        return result[0]

    def close(self) -> None:
        """Kill the subprocess and stop the response thread."""
        self._stop_event.set()
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                self._process.kill()
            self._process = None

    # -- internal --

    def _ensure_process(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return
            cmd = ["python3", "-m", "daglas.lesson.llm"]
            self._process = subprocess.Popen(cmd)
            logger.info("LLM subprocess started pid=%d", self._process.pid)
            if self._resp_thread is None or not self._resp_thread.is_alive():
                self._stop_event.clear()
                self._resp_thread = threading.Thread(
                    target=self._poll_responses, daemon=True
                )
                self._resp_thread.start()

    def _pop_responses(self) -> dict | None:
        """Read and remove the first line from responses.jsonl."""
        with self._lock:
            if not self._responses_path.is_file():
                return None
            lines = self._responses_path.read_text().splitlines()
            if not lines:
                return None
            first = json.loads(lines[0])
            remaining = lines[1:]
            if remaining:
                self._responses_path.write_text("\n".join(remaining) + "\n")
            else:
                self._responses_path.unlink()
        return first

    def _poll_responses(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(0.1)
            item = self._pop_responses()
            if item is None:
                continue
            item_id = item["id"]
            callback = self._pending.pop(item_id, None)
            if "error" in item and item["error"]:
                if callback:
                    callback(item["error"])
            elif "text" in item:
                if callback:
                    callback(item["text"])


# ---------------------------------------------------------------------------
# LLM subprocess entry point
# ---------------------------------------------------------------------------


def _create_provider_from_config() -> LlmProvider:
    """Read config and build the appropriate provider."""
    import daglas.config as daglas_config

    if daglas_config.config is None:
        daglas_config.config = daglas_config.load_config()
    cfg = daglas_config.config

    backend = (cfg.llm_backend or "").lower()
    endpoint = cfg.llm_endpoint or ""
    model = cfg.llm_model or ""
    api_key = cfg.llm_api_key or ""

    if backend == "ollama":
        return LlmOllama(endpoint=endpoint, model=model, api_key=api_key)
    if backend == "llamacpp":
        return LlmLlamaCpp(endpoint=endpoint, model=model, api_key=api_key)
    from daglas.lesson.llm_mlx import LlmMLX

    return LlmMLX(model=model, endpoint=endpoint)


def _pop_jsonl(path: Path) -> dict | None:
    """Read and remove the first line from a JSONL file."""
    if not path.is_file():
        return None
    with open(path) as f:
        lines = f.readlines()
    if not lines:
        return None
    first = json.loads(lines[0])
    remaining = lines[1:]
    if remaining:
        with open(path, "w") as f:
            f.writelines(remaining)
    else:
        path.unlink()
    return first


def _append_jsonl(path: Path, data: dict) -> None:
    """Append a JSON line to a file."""
    with open(path, "a") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")


def main(idle_timeout: float = 20.0):
    """Subprocess entry point. Reads prompts.jsonl, calls provider,
    writes responses.jsonl.  Exits after idle_timeout seconds of empty queue."""
    logging.basicConfig(level=logging.INFO)

    import daglas.config as daglas_config

    if daglas_config.config is None:
        daglas_config.config = daglas_config.load_config()
    cfg = daglas_config.config

    data_dir = Path(cfg.data_dir)
    prompts_path = data_dir / "llm" / "prompts.jsonl"
    responses_path = data_dir / "llm" / "responses.jsonl"
    prompts_path.parent.mkdir(parents=True, exist_ok=True)

    provider = _create_provider_from_config()
    provider.start()
    logger.info("Provider started: %s", type(provider).__name__)

    idle_seconds = 0.0

    try:
        while True:
            time.sleep(0.1)
            item = _pop_jsonl(prompts_path)
            if item is None:
                idle_seconds += 0.1
                if idle_seconds >= idle_timeout:
                    logger.info("Idle timeout reached, exiting")
                    break
                continue

            idle_seconds = 0.0
            try:
                text = provider.prompt(item.get("system", ""), item["prompt"])
                _append_jsonl(responses_path, {"id": item["id"], "text": text})
            except Exception as exc:
                _append_jsonl(responses_path, {"id": item["id"], "error": str(exc)})
    finally:
        provider.stop()
        logger.info("Provider stopped")


if __name__ == "__main__":
    main()
