from __future__ import annotations

import os
import sys
import threading
from unittest.mock import MagicMock, patch

import httpx
import pytest

from daglas.lesson.llm import Llm, VALID_BACKENDS, BACKEND_DEFAULTS, create_llm
from daglas.lesson.llm_mlx import MlxModel, ModelState


def _mock_mlx(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Install a mock mlx_lm module so MlxModel loads without real MLX."""
    mock_tokenizer = MagicMock()
    mock_tokenizer.apply_chat_template.return_value = "<chat>P</chat>"

    mock = MagicMock()
    mock.load.return_value = ("model", mock_tokenizer)
    mock.generate.return_value = "hej världen"
    monkeypatch.setitem(sys.modules, "mlx_lm", mock)

    mock_core = MagicMock()
    monkeypatch.setitem(sys.modules, "mlx", MagicMock())
    monkeypatch.setitem(sys.modules, "mlx.core", mock_core)
    return mock


# =========================================================================
# MlxModel — state machine
# =========================================================================


class TestMlxModelState:
    def test_initial_state(self):
        model = MlxModel()
        assert model.state == ModelState.UNLOADED

    def test_post_transitions_to_ready(self, monkeypatch):
        _mock_mlx(monkeypatch)
        model = MlxModel(idle_timeout=1.0)

        event = threading.Event()
        model.post("hi", callback=lambda t: event.set())
        assert event.wait(timeout=3)

        assert model.state == ModelState.READY

        model.close()

    def test_generation_transition(self, monkeypatch):
        mock = _mock_mlx(monkeypatch)
        model = MlxModel(idle_timeout=0.1)

        event = threading.Event()
        received = []

        def callback(text):
            received.append(text)
            event.set()

        model.post("hej", callback=callback)
        assert event.wait(timeout=3)
        assert received == [mock.generate.return_value]

    def test_prompt_delegates_to_prompt_sync(self):
        model = MlxModel()
        with patch.object(model, "prompt_sync", return_value="hej") as mock_sync:
            result = model.prompt(system="S", user="U")
            assert result == "hej"
            mock_sync.assert_called_once_with(system="S", prompt="U")


class TestMlxModelPostAndDispatch:
    def test_post_enqueues_and_starts_worker(self):
        model = MlxModel(idle_timeout=0.1)

        with patch.object(model, "_ensure_worker") as mock_ensure:
            model.post("hello")
            mock_ensure.assert_called_once()

    def test_prompt_sync_returns_text(self, monkeypatch):
        _mock_mlx(monkeypatch)
        model = MlxModel(idle_timeout=0.1)

        result = model.prompt_sync(system="S", prompt="P")
        assert result is not None

    def test_dispatches_none_on_error(self, monkeypatch):
        _mock_mlx(monkeypatch)
        model = MlxModel(idle_timeout=0.1)

        event = threading.Event()
        received = []

        def callback(text):
            received.append(text)
            event.set()

        with patch.object(model, "_generate", side_effect=ValueError("fail")):
            model.post("hi", callback=callback)

        assert event.wait(timeout=3)
        assert received == [None]

    def test_state_generating_during_call(self, monkeypatch):
        _mock_mlx(monkeypatch)
        model = MlxModel(idle_timeout=0.1)
        state_during_call = []

        orig_generate = model._generate

        def spy_generate(system, user):
            state_during_call.append(model.state)
            return orig_generate(system, user)

        model._generate = spy_generate  # type: ignore[method-assign]

        model.prompt_sync("P")
        assert ModelState.GENERATING in state_during_call


class TestMlxModelIdleLifecycle:
    def test_idle_unloads(self, monkeypatch):
        _mock_mlx(monkeypatch)
        model = MlxModel(idle_timeout=0.05)

        model._ensure_worker()
        model._thread.join(timeout=2)

        assert model.state == ModelState.UNLOADED

    def test_generation_during_idle_completes(self, monkeypatch):
        _mock_mlx(monkeypatch)
        model = MlxModel(idle_timeout=0.05)

        event = threading.Event()
        received = []

        def callback(text):
            received.append(text)
            event.set()

        model.post("hello", callback=callback)
        assert event.wait(timeout=3)
        assert received is not None

    def test_restarts_after_idle(self, monkeypatch):
        _mock_mlx(monkeypatch)
        model = MlxModel(idle_timeout=0.05)

        model._ensure_worker()
        model._thread.join(timeout=2)
        assert model.state == ModelState.UNLOADED

        event = threading.Event()
        received = []

        def callback(text):
            received.append(text)
            event.set()

        model.post("hello", callback=callback)
        assert event.wait(timeout=3)
        assert received is not None


class TestMlxModelClose:
    def test_close_unloads(self, monkeypatch):
        _mock_mlx(monkeypatch)
        model = MlxModel(idle_timeout=1.0)

        model._ensure_worker()
        model.close()
        assert model.state == ModelState.UNLOADED

    def test_close_without_worker(self):
        model = MlxModel()
        model.close()
        assert model.state == ModelState.UNLOADED


class TestMlxModelHfCacheDir:
    def test_sets_hf_home_env(self, monkeypatch):
        _mock_mlx(monkeypatch)
        model = MlxModel(hf_cache_dir="~/.cache/hf")

        model._load_model()
        assert os.environ.get("HF_HOME", "").endswith(".cache/hf")

    def test_does_not_set_when_empty(self, monkeypatch):
        _mock_mlx(monkeypatch)
        model = MlxModel(hf_cache_dir="")
        old_hf = os.environ.get("HF_HOME", "__UNSET__")

        model._load_model()
        assert os.environ.get("HF_HOME", "__UNSET__") == old_hf


# =========================================================================
# Llm — HTTP dispatch (unchanged)
# =========================================================================


class TestCallLlm:
    def test_posts_to_correct_endpoint(self):
        llm = Llm(endpoint="http://test:8080/v1", model="m")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "hej"}}]}

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            result = llm._call_llm(system="S", user="U")
            assert result == "hej"
            mock_post.assert_called_once_with(
                "http://test:8080/v1/chat/completions",
                headers={"Content-Type": "application/json"},
                json={
                    "messages": [
                        {"role": "system", "content": "S"},
                        {"role": "user", "content": "U"},
                    ],
                    "stream": False,
                    "max_tokens": 2048,
                    "model": "m",
                },
                timeout=300,
            )

    def test_sends_api_key(self):
        llm = Llm(endpoint="http://test", api_key="secret")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            llm._call_llm("", "")
            assert mock_post.call_args[1]["headers"]["Authorization"] == "Bearer secret"

    def test_omits_model_when_empty(self):
        llm = Llm(endpoint="http://test", model="")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            llm._call_llm("", "")
            assert "model" not in mock_post.call_args[1]["json"]

    def test_raises_on_http_error(self):
        llm = Llm(endpoint="http://test")
        with patch(
            "httpx.post",
            side_effect=httpx.HTTPStatusError(
                "error", request=MagicMock(), response=MagicMock()
            ),
        ):
            with pytest.raises(httpx.HTTPStatusError):
                llm._call_llm("", "")


class TestLlmWorkerLifecycle:
    def test_ensure_worker_starts_thread(self):
        llm = Llm(endpoint="http://test", idle_timeout=1.0)
        llm._ensure_worker()
        assert llm._thread is not None
        assert llm._thread.is_alive()

    def test_ensure_worker_no_duplicate(self):
        llm = Llm(endpoint="http://test", idle_timeout=1.0)
        llm._ensure_worker()
        t1 = llm._thread
        llm._ensure_worker()
        assert llm._thread is t1

    def test_post_invokes_callback(self):
        llm = Llm(endpoint="http://test", idle_timeout=0.1)
        event = threading.Event()
        received = []

        with patch.object(llm, "_call_llm", return_value="world"):
            llm.post("hello", callback=lambda t: [received.append(t), event.set()])

        assert event.wait(timeout=2)
        assert received == ["world"]

    def test_dispatches_none_on_error(self):
        llm = Llm(endpoint="http://test", idle_timeout=0.1)
        event = threading.Event()
        received = []

        with patch.object(llm, "_call_llm", side_effect=ValueError("fail")):
            llm.post("hello", callback=lambda t: [received.append(t), event.set()])

        assert event.wait(timeout=2)
        assert received == [None]

    def test_exits_on_idle(self):
        llm = Llm(endpoint="http://test", idle_timeout=0.02)
        event = threading.Event()

        with patch.object(llm, "_call_llm", return_value="ok"):
            llm.post("hi", callback=lambda t: event.set())

        assert event.wait(timeout=2)

        if llm._thread is not None:
            llm._thread.join(timeout=1)
        assert llm._thread is None or not llm._thread.is_alive()

    def test_prompt_sync_returns_result(self):
        llm = Llm(endpoint="http://test", idle_timeout=0.1)
        with patch.object(llm, "_call_llm", return_value="hej"):
            result = llm.prompt_sync(system="S", prompt="P")
            assert result == "hej"

    def test_prompt_sync_returns_none_on_error(self):
        llm = Llm(endpoint="http://test", idle_timeout=0.1)
        with patch.object(llm, "_call_llm", side_effect=ValueError("fail")):
            result = llm.prompt_sync(system="S", prompt="P")
            assert result is None


class TestLlmClose:
    def test_stops_worker(self):
        llm = Llm(endpoint="http://test", idle_timeout=1.0)
        llm._ensure_worker()
        llm.close()
        assert llm._thread is None or not llm._thread.is_alive()

    def test_close_without_worker(self):
        llm = Llm(endpoint="http://test")
        llm.close()


# =========================================================================
# create_llm factory
# =========================================================================


class TestCreateLlm:
    def _cfg(self, **overrides):
        cfg = MagicMock()
        cfg.llm_backend = "ollama"
        cfg.llm_endpoint = ""
        cfg.llm_model = ""
        cfg.llm_api_key = ""
        cfg.llm_max_tokens = 2048
        cfg.llm_idle_timeout = 20.0
        cfg.hf_cache_dir = ""
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return cfg

    def test_ollama_returns_llm(self):
        cfg = self._cfg(llm_backend="ollama")
        llm = create_llm(cfg)
        assert isinstance(llm, Llm)
        assert llm._endpoint == BACKEND_DEFAULTS["ollama"]["endpoint"]
        assert llm._model == BACKEND_DEFAULTS["ollama"]["model"]

    def test_mlx_local_returns_mlx_model(self):
        cfg = self._cfg(llm_backend="mlx_local")
        llm = create_llm(cfg)
        assert isinstance(llm, MlxModel)

    def test_mlx_alias_returns_mlx_model(self):
        cfg = self._cfg(llm_backend="mlx")
        llm = create_llm(cfg)
        assert isinstance(llm, MlxModel)

    def test_llamacpp_returns_llm(self):
        cfg = self._cfg(llm_backend="llamacpp")
        llm = create_llm(cfg)
        assert isinstance(llm, Llm)

    def test_override_endpoint(self):
        cfg = self._cfg(llm_backend="ollama", llm_endpoint="http://custom:8080/v1")
        llm = create_llm(cfg)
        assert isinstance(llm, Llm)
        assert llm._endpoint == "http://custom:8080/v1"

    def test_custom_api_key(self):
        cfg = self._cfg(llm_backend="ollama", llm_api_key="sk-custom")
        llm = create_llm(cfg)
        assert isinstance(llm, Llm)
        assert llm._api_key == "sk-custom"

    def test_mlx_local_uses_hf_cache_dir(self):
        cfg = self._cfg(llm_backend="mlx", hf_cache_dir="~/.cache/hf")
        llm = create_llm(cfg)
        assert isinstance(llm, MlxModel)
        assert llm._hf_cache_dir == "~/.cache/hf"

    def test_unknown_backend_raises(self):
        cfg = self._cfg(llm_backend="unknown")
        with pytest.raises(ValueError, match="unknown"):
            create_llm(cfg)


# =========================================================================
# Module constants
# =========================================================================


class TestConstants:
    def test_valid_backends(self):
        assert VALID_BACKENDS == {"ollama", "llamacpp", "mlx_local"}

    def test_backend_defaults_keys(self):
        assert set(BACKEND_DEFAULTS.keys()) == VALID_BACKENDS

    def test_mlx_local_defaults(self):
        d = BACKEND_DEFAULTS["mlx_local"]
        assert "model" in d
        assert "endpoint" not in d
