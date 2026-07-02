from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from daglas.lesson.llm import (
    Llm,
    LlmLlamaCpp,
    LlmOllama,
    _create_provider_from_config,
    _pop_jsonl,
    _append_jsonl,
)


# =========================================================================
# Provider tests
# =========================================================================


class TestLlmOllama:
    def test_start_stop_noop(self):
        provider = LlmOllama()
        provider.start()
        provider.stop()

    def test_prompt(self):
        provider = LlmOllama(endpoint="http://localhost:11434/v1", model="test-model")
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hej världen"}}]
        }

        with patch("httpx.post", return_value=mock_response) as mock_post:
            result = provider.prompt(system="You are a teacher", user="Hello")
            assert result == "Hej världen"
            mock_post.assert_called_once()

    def test_prompt_sends_api_key(self):
        provider = LlmOllama(api_key="secret-key")
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}

        with patch("httpx.post", return_value=mock_response) as mock_post:
            provider.prompt(system="", user="")
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["headers"]["Authorization"] == "Bearer secret-key"

    def test_raises_on_http_error(self):
        provider = LlmOllama()
        with patch(
            "httpx.post",
            side_effect=httpx.HTTPStatusError(
                "error", request=MagicMock(), response=MagicMock()
            ),
        ):
            with pytest.raises(httpx.HTTPStatusError):
                provider.prompt(system="", user="")


class TestLmLlamaCpp:
    def test_start_stop_noop(self):
        provider = LlmLlamaCpp()
        provider.start()
        provider.stop()

    def test_prompt(self):
        provider = LlmLlamaCpp(endpoint="http://localhost:8080/v1")
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"choices": [{"message": {"content": "Hej"}}]}

        with patch("httpx.post", return_value=mock_response):
            result = provider.prompt(system="", user="")
            assert result == "Hej"


class TestLlmMLX:
    def test_start_stop(self):
        from daglas.lesson.llm_mlx import LlmMLX

        provider = LlmMLX(model="dummy-model")
        assert provider._state is None

        with patch("mlx_lm.load", return_value=("mock_model", "mock_tokenizer")):
            provider.start()
            assert provider._state is not None

        provider.stop()
        assert provider._state is None

    def test_prompt(self):
        from daglas.lesson.llm_mlx import LlmMLX

        mock_tokenizer = MagicMock()
        mock_tokenizer.apply_chat_template.return_value = "formatted prompt"

        provider = LlmMLX(model="dummy-model")
        provider._state = ("mock_model", mock_tokenizer)

        with patch("mlx_lm.generate", return_value="Svenskt svar") as mock_gen:
            result = provider.prompt(system="System", user="User")
            assert result == "Svenskt svar"
            mock_tokenizer.apply_chat_template.assert_called_once()
            mock_gen.assert_called_once()


# =========================================================================
# JSONL helpers
# =========================================================================


class TestJsonlHelpers:
    def test_pop_jsonl_empty_file(self, tmp_path):
        f = tmp_path / "test.jsonl"
        assert _pop_jsonl(f) is None

    def test_pop_jsonl_reads_first_line(self, tmp_path):
        f = tmp_path / "test.jsonl"
        f.write_text('{"a": 1}\n{"a": 2}\n')
        assert _pop_jsonl(f) == {"a": 1}
        assert f.read_text() == '{"a": 2}\n'

    def test_pop_jsonl_removes_file_when_last_line(self, tmp_path):
        f = tmp_path / "test.jsonl"
        f.write_text('{"a": 1}\n')
        assert _pop_jsonl(f) == {"a": 1}
        assert not f.exists()

    def test_append_jsonl(self, tmp_path):
        f = tmp_path / "test.jsonl"
        _append_jsonl(f, {"b": 2})
        assert f.read_text() == '{"b": 2}\n'
        _append_jsonl(f, {"c": 3})
        assert f.read_text() == '{"b": 2}\n{"c": 3}\n'


# =========================================================================
# _create_provider_from_config
# =========================================================================


class TestCreateProviderFromConfig:
    @pytest.fixture(autouse=True)
    def _setup(self):
        with patch("daglas.config.config") as mock_cfg:
            mock_cfg.llm_backend = ""
            mock_cfg.llm_endpoint = ""
            mock_cfg.llm_model = "test-model"
            mock_cfg.llm_api_key = ""
            yield

    def test_default_mlx(self):
        provider = _create_provider_from_config()
        from daglas.lesson.llm_mlx import LlmMLX

        assert isinstance(provider, LlmMLX)

    def test_ollama_backend(self):
        with patch("daglas.config.config") as mock_cfg:
            mock_cfg.llm_backend = "ollama"
            mock_cfg.llm_endpoint = "http://localhost:11434/v1"
            mock_cfg.llm_model = "test-model"
            mock_cfg.llm_api_key = ""
            provider = _create_provider_from_config()
            assert isinstance(provider, LlmOllama)
            assert provider._endpoint == "http://localhost:11434/v1"
            assert provider._model == "test-model"

    def test_mlx_backend(self):
        with patch("daglas.config.config") as mock_cfg:
            mock_cfg.llm_backend = "mlx"
            mock_cfg.llm_endpoint = ""
            mock_cfg.llm_model = "mlx-model"
            mock_cfg.llm_api_key = ""
            provider = _create_provider_from_config()
            from daglas.lesson.llm_mlx import LlmMLX

            assert isinstance(provider, LlmMLX)

    def test_llamacpp_backend(self):
        with patch("daglas.config.config") as mock_cfg:
            mock_cfg.llm_backend = "llamacpp"
            mock_cfg.llm_endpoint = "http://localhost:8080/v1"
            mock_cfg.llm_model = ""
            mock_cfg.llm_api_key = ""
            provider = _create_provider_from_config()
            assert isinstance(provider, LlmLlamaCpp)


# =========================================================================
# Llm class tests
# =========================================================================


class TestLlm:
    @pytest.fixture
    def llm(self, tmp_path):
        return Llm(data_dir=str(tmp_path))

    def test_post_writes_to_prompts_jsonl(self, llm, tmp_path):
        prompts_path = tmp_path / "llm" / "prompts.jsonl"

        with patch.object(llm, "_ensure_process"):
            llm.post("Hello", system="System")

        lines = prompts_path.read_text().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["prompt"] == "Hello"
        assert data["system"] == "System"
        assert "id" in data
        assert "queued_at" in data

    def test_post_ensures_process(self, llm):
        with patch.object(llm, "_ensure_process") as mock_ensure:
            llm.post("Hello")
            mock_ensure.assert_called_once()

    def test_ensure_process_spawns_subprocess(self, llm, tmp_path):
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            llm._ensure_process()

            mock_popen.assert_called_once_with(["python3", "-m", "daglas.lesson.llm"])

    def test_ensure_process_does_not_spawn_twice(self, llm):
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            llm._ensure_process()
            llm._ensure_process()

            assert mock_popen.call_count == 1

    def test_pop_responses(self, llm, tmp_path):
        responses_path = tmp_path / "llm" / "responses.jsonl"
        responses_path.parent.mkdir(parents=True, exist_ok=True)
        responses_path.write_text('{"id": "abc", "text": "hello"}\n')

        item = llm._pop_responses()
        assert item == {"id": "abc", "text": "hello"}
        assert not responses_path.exists()

    def test_pop_responses_none_when_empty(self, llm):
        assert llm._pop_responses() is None

    def test_pop_responses_with_thread_lock(self, llm, tmp_path):
        responses_path = tmp_path / "llm" / "responses.jsonl"
        responses_path.parent.mkdir(parents=True, exist_ok=True)
        responses_path.write_text('{"id": "x", "text": "y"}\n')

        item = llm._pop_responses()
        assert item == {"id": "x", "text": "y"}

    def test_close_kills_subprocess(self, llm):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        llm._process = mock_proc

        llm.close()
        mock_proc.terminate.assert_called_once()

    def test_close_no_process(self, llm):
        llm.close()

    def test_prompt_sync_calls_post(self, llm):
        with patch.object(llm, "post") as mock_post:

            def _fake_post(prompt, system="", callback=None):
                if callback:
                    callback("mocked")

            mock_post.side_effect = _fake_post
            result = llm.prompt_sync(system="S", prompt="P")
            assert result == "mocked"
            mock_post.assert_called_once()

    def test_prompt_convenience(self, llm):
        with patch.object(llm, "prompt_sync", return_value="hej") as mock_sync:
            result = llm.prompt(system="S", user="U")
            assert result == "hej"
            mock_sync.assert_called_once_with(system="S", prompt="U")

    def test_dispatch_invokes_callback(self, llm, tmp_path):
        responses_path = tmp_path / "llm" / "responses.jsonl"
        responses_path.parent.mkdir(parents=True, exist_ok=True)

        received = []

        def callback(text):
            received.append(text)

        llm._pending["test-id"] = callback
        responses_path.write_text('{"id": "test-id", "text": "callback ok"}\n')

        item = llm._pop_responses()
        assert item is not None
        cb = llm._pending.pop(item["id"], None)
        assert cb is not None
        cb(item.get("text", ""))

        assert received == ["callback ok"]

    def test_dispatch_handles_error(self, llm, tmp_path):
        responses_path = tmp_path / "llm" / "responses.jsonl"
        responses_path.parent.mkdir(parents=True, exist_ok=True)

        received = []

        def callback(text):
            received.append(text)

        llm._pending["test-id"] = callback
        responses_path.write_text('{"id": "test-id", "error": "something broke"}\n')

        item = llm._pop_responses()
        assert item is not None
        assert "error" in item
        cb = llm._pending.pop(item["id"], None)
        if cb:
            cb(item["error"])

        assert received == ["something broke"]


# =========================================================================
# Subprocess main() tests
# =========================================================================


class TestMain:
    """main() reads data_dir from config. Patch config to point at tmp_path."""

    @pytest.fixture
    def mock_cfg(self, tmp_path):
        with patch("daglas.config.config") as cfg:
            cfg.data_dir = str(tmp_path)
            cfg.llm_backend = ""
            cfg.llm_endpoint = "http://localhost:11434/v1"
            cfg.llm_model = "test-model"
            cfg.llm_api_key = ""
            yield cfg

    def test_main_processes_prompt(self, tmp_path, mock_cfg):
        prompts_path = tmp_path / "llm" / "prompts.jsonl"
        responses_path = tmp_path / "llm" / "responses.jsonl"
        prompts_path.parent.mkdir(parents=True, exist_ok=True)

        _append_jsonl(
            prompts_path,
            {"id": "test-1", "system": "", "prompt": "Hello"},
        )

        mock_provider = MagicMock()
        mock_provider.prompt.return_value = "response text"

        from daglas.lesson.llm import main as _main

        with (
            patch(
                "daglas.lesson.llm._create_provider_from_config",
                return_value=mock_provider,
            ),
            patch("daglas.lesson.llm.logging"),
            patch("daglas.lesson.llm.time.sleep"),
        ):
            _main(idle_timeout=0.2)

        assert responses_path.is_file()
        data = json.loads(responses_path.read_text().strip())
        assert data["id"] == "test-1"
        assert data["text"] == "response text"

    def test_main_idle_timeout(self, mock_cfg):
        mock_provider = MagicMock()

        with (
            patch(
                "daglas.lesson.llm._create_provider_from_config",
                return_value=mock_provider,
            ),
            patch("daglas.lesson.llm.logging"),
            patch("daglas.lesson.llm.time.sleep"),
        ):
            from daglas.lesson.llm import main as _main

            _main(idle_timeout=0.1)

        mock_provider.stop.assert_called_once()

    def test_main_error_writes_error(self, tmp_path, mock_cfg):
        prompts_path = tmp_path / "llm" / "prompts.jsonl"
        responses_path = tmp_path / "llm" / "responses.jsonl"
        prompts_path.parent.mkdir(parents=True, exist_ok=True)

        _append_jsonl(
            prompts_path,
            {"id": "err-1", "system": "", "prompt": "Hi"},
        )

        mock_provider = MagicMock()
        mock_provider.prompt.side_effect = ValueError("LLM failed")

        with (
            patch(
                "daglas.lesson.llm._create_provider_from_config",
                return_value=mock_provider,
            ),
            patch("daglas.lesson.llm.logging"),
            patch("daglas.lesson.llm.time.sleep"),
        ):
            from daglas.lesson.llm import main as _main

            _main(idle_timeout=0.2)

        data = json.loads(responses_path.read_text().strip())
        assert data["id"] == "err-1"
        assert "LLM failed" in data["error"]

    def test_main_stops_provider_on_exit(self, mock_cfg):
        mock_provider = MagicMock()

        with (
            patch(
                "daglas.lesson.llm._create_provider_from_config",
                return_value=mock_provider,
            ),
            patch("daglas.lesson.llm.logging"),
            patch("daglas.lesson.llm.time.sleep"),
        ):
            from daglas.lesson.llm import main as _main

            _main(idle_timeout=0.1)

        mock_provider.stop.assert_called_once()
