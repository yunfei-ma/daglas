"""
Deprecated — MLX backend is now managed via ``mlx_lm.server``.

Use ``daglas.lesson.llm.create_llm(cfg)`` with
``cfg.llm_backend = "mlx_server"`` instead.
"""

from __future__ import annotations


class LlmMLX:
    def __init__(
        self,
        model: str = "mlx-community/Llama-3.2-3B-Instruct-4bit",
        endpoint: str = "",
        max_tokens: int = 2048,
    ):
        self._model = model
        self._max_tokens = max_tokens
        self._state = None

    def start(self) -> None:
        import mlx_lm

        self._state = mlx_lm.load(self._model)

    def stop(self) -> None:
        self._state = None

    def prompt(self, system: str, user: str) -> str:
        import mlx_lm

        model, tokenizer = self._state
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        prompt = tokenizer.apply_chat_template(messages, add_generation_prompt=True)
        return mlx_lm.generate(model, tokenizer, prompt, max_tokens=self._max_tokens)
