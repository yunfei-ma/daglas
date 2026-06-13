from unittest.mock import MagicMock, patch

import httpx
import pytest

from daglas.lesson.llm import (
    LlamaCppProvider,
    MlxProvider,
    OllamaProvider,
    create_provider,
)


class TestProviders:
    def test_ollama_provider_prompt(self):
        provider = OllamaProvider(
            endpoint="http://localhost:11434/v1", model="test-model"
        )
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hej världen"}}]
        }

        with patch("httpx.post", return_value=mock_response) as mock_post:
            result = provider.prompt(system="You are a teacher", user="Hello")
            assert result == "Hej världen"
            mock_post.assert_called_once()

    def test_mlx_provider_prompt(self):
        provider = MlxProvider(endpoint="http://localhost:8080", model="test-model")
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"choices": [{"message": {"content": "Hej"}}]}

        with patch("httpx.post", return_value=mock_response):
            result = provider.prompt(system="", user="")
            assert result == "Hej"

    def test_llamacpp_provider_prompt(self):
        provider = LlamaCppProvider(endpoint="http://localhost:8080/v1")
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"choices": [{"message": {"content": "Hej"}}]}

        with patch("httpx.post", return_value=mock_response):
            result = provider.prompt(system="", user="")
            assert result == "Hej"

    def test_create_provider_default(self):
        provider = create_provider()
        assert isinstance(provider, OllamaProvider)

    def test_create_provider_ollama(self):
        provider = create_provider(endpoint="http://localhost:11434/v1")
        assert isinstance(provider, OllamaProvider)

    def test_provider_raises_on_http_error(self):
        provider = OllamaProvider()
        with patch(
            "httpx.post",
            side_effect=httpx.HTTPStatusError(
                "error", request=MagicMock(), response=MagicMock()
            ),
        ):
            with pytest.raises(httpx.HTTPStatusError):
                provider.prompt(system="", user="")
