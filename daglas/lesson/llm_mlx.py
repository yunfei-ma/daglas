from __future__ import annotations

import enum
import gc
import logging
import os
import queue
import threading
import uuid
from collections.abc import Callable


logger = logging.getLogger(__name__)


class ModelState(enum.Enum):
    UNLOADED = "unloaded"
    LOADING = "loading"
    READY = "ready"
    GENERATING = "generating"


class MlxModel:
    """In-process MLX inference with threaded queue and idle unload."""

    def __init__(
        self,
        *,
        model: str = "mlx-community/Llama-3.2-3B-Instruct-4bit",
        max_tokens: int = 2048,
        idle_timeout: float = 20.0,
        hf_cache_dir: str = "",
        enable_thinking: bool = False,
    ):
        self._model = model
        self._max_tokens = max_tokens
        self._idle_timeout = idle_timeout
        self._hf_cache_dir = hf_cache_dir
        # Gemma-4 only: turns on the hidden reasoning channel. Managed via
        # config.yaml `llm_enable_thinking`; do NOT enable for other models —
        # non-Gemma chat templates ignore or reject this kwarg.
        self._enable_thinking = enable_thinking

        self._state: ModelState = ModelState.UNLOADED
        self._model_ref: tuple | None = None

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

    @property
    def state(self) -> ModelState:
        return self._state

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._thread.start()

    def _load_model(self) -> None:
        self._state = ModelState.LOADING
        if self._hf_cache_dir:
            os.environ["HF_HOME"] = os.path.expanduser(self._hf_cache_dir)
        import mlx_lm

        self._model_ref = mlx_lm.load(self._model)
        self._state = ModelState.READY

    def _unload_model(self) -> None:
        if self._model_ref is not None:
            self._model_ref = None
            gc.collect()
            try:
                import mlx.core as mx

                mx.clear_cache()
            except ImportError:
                pass
        self._state = ModelState.UNLOADED

    def _worker_loop(self) -> None:
        try:
            self._load_model()
        except Exception:
            logger.exception("MlxModel load failed")
            return

        try:
            while not self._stop_event.is_set():
                try:
                    item = self._queue.get(timeout=self._idle_timeout)
                except queue.Empty:
                    break

                if item is None:
                    break

                self._state = ModelState.GENERATING
                try:
                    text = self._generate(item.get("system", ""), item["prompt"])
                    self._dispatch(item["id"], text)
                except Exception:
                    logger.exception("MLX generation failed")
                    self._dispatch(item["id"], None)
                finally:
                    self._state = ModelState.READY
        finally:
            self._unload_model()

    def _generate(self, system: str, user: str) -> str:
        import mlx_lm

        model, tokenizer = self._model_ref
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        # `enable_thinking` is a Gemma-4-only chat-template option. Only pass
        # it when config.yaml `llm_enable_thinking` is true — for any other
        # model the kwarg is ignored (or rejected), so never enable by default.
        if self._enable_thinking:
            prompt_text = tokenizer.apply_chat_template(
                messages, add_generation_prompt=True, enable_thinking=True
            )
        else:
            prompt_text = tokenizer.apply_chat_template(
                messages, add_generation_prompt=True
            )
        return mlx_lm.generate(
            model, tokenizer, prompt_text, max_tokens=self._max_tokens
        )

    def _dispatch(self, item_id: str, text: str | None) -> None:
        callback = self._pending.pop(item_id, None)
        if callback:
            callback(text)
