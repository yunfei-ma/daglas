from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import httpx
import pytest

from daglas.lesson.llm import Llm, create_llm, VALID_BACKENDS, BACKEND_DEFAULTS


# =========================================================================
# Internal helpers: _start_server / _stop_server
# =========================================================================


class TestStartServer:
    def test_spawns_when_managing(self):
        llm = Llm(
            endpoint="http://test", manage_process=True, server_cmd=["echo", "hello"]
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with (
            patch("subprocess.Popen") as mock_popen,
            patch("httpx.get", return_value=mock_resp),
        ):
            llm._start_server()
            mock_popen.assert_called_once_with(["echo", "hello"])

    def test_noop_when_not_managing(self):
        llm = Llm(endpoint="http://test")
        with patch("subprocess.Popen") as mock_popen, patch("httpx.get"):
            llm._start_server()
            mock_popen.assert_not_called()

    def test_noop_when_no_cmd(self):
        llm = Llm(endpoint="http://test", manage_process=True)
        with patch("subprocess.Popen") as mock_popen, patch("httpx.get"):
            llm._start_server()
            mock_popen.assert_not_called()


class TestStopServer:
    def test_terminates_process(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        llm = Llm(endpoint="http://test", manage_process=True)
        llm._process = mock_proc
        llm._stop_server()
        mock_proc.terminate.assert_called_once()
        assert llm._process is None

    def test_kills_on_terminate_timeout(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.terminate.side_effect = OSError("timeout")
        llm = Llm(endpoint="http://test", manage_process=True)
        llm._process = mock_proc
        llm._stop_server()
        mock_proc.kill.assert_called_once()

    def test_noop_when_not_managing(self):
        mock_proc = MagicMock()
        llm = Llm(endpoint="http://test", manage_process=False)
        llm._process = mock_proc
        llm._stop_server()
        mock_proc.terminate.assert_not_called()

    def test_noop_when_no_process(self):
        llm = Llm(endpoint="http://test", manage_process=True)
        llm._stop_server()


# =========================================================================
# _call_llm HTTP method
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


# =========================================================================
# Worker lifecycle: _ensure_worker
# =========================================================================


class TestEnsureWorker:
    def test_starts_thread(self):
        llm = Llm(endpoint="http://test", idle_timeout=1.0)
        llm._ensure_worker()
        assert llm._thread is not None
        assert llm._thread.is_alive()

    def test_no_duplicate(self):
        llm = Llm(endpoint="http://test", idle_timeout=1.0)
        llm._ensure_worker()
        t1 = llm._thread
        llm._ensure_worker()
        assert llm._thread is t1

    def test_restarts_after_exit(self):
        llm = Llm(endpoint="http://test", idle_timeout=0.02)
        event = threading.Event()

        with patch.object(llm, "_call_llm", return_value="ok"):
            llm.post("hi", callback=lambda t: event.set())

        assert event.wait(timeout=2)
        t1 = llm._thread

        if t1 is not None:
            t1.join(timeout=1)

        llm._ensure_worker()
        t2 = llm._thread
        assert t2 is not None and t2.is_alive()


# =========================================================================
# Post and dispatch
# =========================================================================


class TestPost:
    def test_calls_ensure_worker(self):
        llm = Llm(endpoint="http://test", idle_timeout=1.0)
        with patch.object(llm, "_ensure_worker") as mock_ensure:
            llm.post("hello")
            mock_ensure.assert_called_once()

    def test_invokes_callback(self):
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


# =========================================================================
# prompt_sync and prompt convenience
# =========================================================================


class TestPromptSync:
    def test_returns_result(self):
        llm = Llm(endpoint="http://test", idle_timeout=0.1)
        with patch.object(llm, "_call_llm", return_value="hej"):
            result = llm.prompt_sync(system="S", prompt="P")
            assert result == "hej"

    def test_returns_none_on_error(self):
        llm = Llm(endpoint="http://test", idle_timeout=0.1)
        with patch.object(llm, "_call_llm", side_effect=ValueError("fail")):
            result = llm.prompt_sync(system="S", prompt="P")
            assert result is None


class TestPrompt:
    def test_delegates_to_prompt_sync(self):
        llm = Llm(endpoint="http://test")
        with patch.object(llm, "prompt_sync", return_value="hej") as mock_sync:
            result = llm.prompt(system="S", user="U")
            assert result == "hej"
            mock_sync.assert_called_once_with(system="S", prompt="U")


# =========================================================================
# Worker loop: idle exit and server cleanup
# =========================================================================


class TestWorkerLoop:
    def test_exits_on_idle(self):
        llm = Llm(endpoint="http://test", idle_timeout=0.02)
        event = threading.Event()

        with patch.object(llm, "_call_llm", return_value="ok"):
            llm.post("hi", callback=lambda t: event.set())

        assert event.wait(timeout=2)

        if llm._thread is not None:
            llm._thread.join(timeout=1)
        assert llm._thread is None or not llm._thread.is_alive()

    def test_stops_server_after_idle(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        llm = Llm(
            endpoint="http://test",
            idle_timeout=0.02,
            manage_process=True,
            server_cmd=["echo"],
        )
        llm._process = mock_proc

        event = threading.Event()
        with (
            patch.object(llm, "_call_llm", return_value="ok"),
            patch.object(llm, "_start_server"),
        ):
            llm.post("hi", callback=lambda t: event.set())

        assert event.wait(timeout=2)

        if llm._thread is not None:
            llm._thread.join(timeout=1)

        mock_proc.terminate.assert_called_once()


# =========================================================================
# Close
# =========================================================================


class TestClose:
    def test_stops_worker(self):
        llm = Llm(endpoint="http://test", idle_timeout=1.0)
        llm._ensure_worker()
        llm.close()
        assert llm._thread is None or not llm._thread.is_alive()

    def test_kills_server(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        llm = Llm(endpoint="http://test", manage_process=True)
        llm._process = mock_proc
        llm._ensure_worker()

        llm.close()
        mock_proc.terminate.assert_called_once()

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
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return cfg

    def test_ollama(self):
        cfg = self._cfg(llm_backend="ollama")
        llm = create_llm(cfg)
        assert llm._endpoint == BACKEND_DEFAULTS["ollama"]["endpoint"]
        assert llm._model == BACKEND_DEFAULTS["ollama"]["model"]
        assert not llm._manage_process

    def test_mlx_server(self):
        cfg = self._cfg(llm_backend="mlx_server")
        llm = create_llm(cfg)
        assert llm._endpoint == BACKEND_DEFAULTS["mlx_server"]["endpoint"]
        assert llm._manage_process
        assert llm._server_cmd == BACKEND_DEFAULTS["mlx_server"]["server_cmd"]

    def test_llamacpp(self):
        cfg = self._cfg(llm_backend="llamacpp")
        llm = create_llm(cfg)
        assert not llm._manage_process

    def test_override_endpoint(self):
        cfg = self._cfg(llm_backend="ollama", llm_endpoint="http://custom:8080/v1")
        llm = create_llm(cfg)
        assert llm._endpoint == "http://custom:8080/v1"

    def test_uses_custom_api_key(self):
        cfg = self._cfg(llm_backend="ollama", llm_api_key="sk-custom")
        llm = create_llm(cfg)
        assert llm._api_key == "sk-custom"

    def test_mlx_alias_maps_to_mlx_server(self):
        cfg = self._cfg(llm_backend="mlx")
        llm = create_llm(cfg)
        assert llm._manage_process

    def test_unknown_backend_raises(self):
        cfg = self._cfg(llm_backend="unknown")
        with pytest.raises(ValueError, match="unknown"):
            create_llm(cfg)


# =========================================================================
# Module constants
# =========================================================================


class TestConstants:
    def test_valid_backends(self):
        assert VALID_BACKENDS == {"ollama", "llamacpp", "mlx_server"}

    def test_backend_defaults_keys(self):
        assert set(BACKEND_DEFAULTS.keys()) == VALID_BACKENDS
