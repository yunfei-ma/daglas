from typing import Protocol

import httpx


class LlmProvider(Protocol):
    def prompt(self, system: str, user: str) -> str: ...


class OllamaProvider:
    def __init__(
        self,
        endpoint: str = "http://localhost:11434/v1",
        model: str = "",
        api_key: str = "",
    ):
        self.endpoint = endpoint.rstrip("/")
        self.model = model or "llama3.2"
        self.api_key = api_key

    def prompt(self, system: str, user: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }

        resp = httpx.post(
            f"{self.endpoint}/chat/completions",
            headers=headers,
            json=body,
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


class MlxProvider:
    def __init__(
        self,
        endpoint: str = "http://localhost:8080",
        model: str = "",
        api_key: str = "",
    ):
        self.endpoint = endpoint.rstrip("/")
        self.model = model or "mlx-community/llama-3.2-3b"
        self.api_key = api_key

    def prompt(self, system: str, user: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        body = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": 2048,
        }

        resp = httpx.post(
            f"{self.endpoint}/chat/completions",
            headers=headers,
            json=body,
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


class LlamaCppProvider:
    def __init__(
        self,
        endpoint: str = "http://localhost:8080/v1",
        model: str = "",
        api_key: str = "",
    ):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.api_key = api_key

    def prompt(self, system: str, user: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        body = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": 2048,
        }

        resp = httpx.post(
            f"{self.endpoint}/chat/completions",
            headers=headers,
            json=body,
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


def create_provider(
    endpoint: str = "",
    model: str = "",
    api_key: str = "",
) -> LlmProvider:
    if not endpoint:
        return OllamaProvider(model=model)
    if "11434" in endpoint:
        return OllamaProvider(endpoint=endpoint, model=model, api_key=api_key)
    if "mlx" in endpoint.lower():
        return MlxProvider(endpoint=endpoint, model=model, api_key=api_key)
    return LlamaCppProvider(endpoint=endpoint, model=model, api_key=api_key)
