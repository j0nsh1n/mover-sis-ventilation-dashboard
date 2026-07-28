"""Ollama lifecycle helper tests (mocked)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.llm.ollama_client import OllamaError
from src.llm.service import (
    ensure_ollama,
    find_ollama_binary,
    ollama_status_summary,
    probe_ollama,
    stop_ollama,
    unload_ollama_model,
)


def test_find_ollama_binary_env(tmp_path, monkeypatch):
    fake = tmp_path / "ollama"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("OLLAMA_PATH", str(fake))
    assert find_ollama_binary() == str(fake)


def test_probe_already_running():
    with patch("src.llm.service.OllamaClient") as Cls:
        inst = Cls.return_value
        inst.list_models.return_value = ["gemma4:latest"]
        st = probe_ollama()
    assert st.available
    assert "gemma4" in st.models[0]


def test_ensure_already_running():
    with patch("src.llm.service.OllamaClient") as Cls:
        inst = Cls.return_value
        inst.list_models.return_value = ["gemma4:latest"]
        st = ensure_ollama(start_if_needed=True)
    assert st.available
    assert "gemma4" in st.models[0]
    assert st.started_by_app is False
    assert "OK" in ollama_status_summary(st)


def test_ensure_starts_when_down():
    with patch("src.llm.service.OllamaClient") as Cls, patch(
        "src.llm.service.find_ollama_binary", return_value="/usr/bin/ollama"
    ), patch("src.llm.service.subprocess.Popen") as Popen, patch(
        "src.llm.service.time.sleep"
    ):
        call_count = {"n": 0}

        def client_factory(*a, **k):
            m = MagicMock()

            def list_models():
                call_count["n"] += 1
                # Stay down until serve is spawned, then succeed
                if Popen.call_count == 0:
                    raise OllamaError("down")
                return ["qwen2.5:14b"]

            m.list_models.side_effect = list_models
            return m

        Cls.side_effect = client_factory
        proc = MagicMock()
        proc.poll.return_value = None
        Popen.return_value = proc

        st = ensure_ollama(start_if_needed=True, wait_s=2.0)
        assert st.available
        assert st.started_by_app is True
        Popen.assert_called()


def test_unload_model_ok():
    with patch("src.llm.service.OllamaClient") as Cls:
        inst = Cls.return_value
        ok, msg = unload_ollama_model("qwen3:14b")
        assert ok
        assert "Unloaded" in msg
        inst.unload_model.assert_called_once_with("qwen3:14b")


def test_unload_model_empty_name():
    ok, msg = unload_ollama_model("(Ollama stopped)")
    assert not ok


def test_stop_ollama_linux():
    with patch("src.llm.service.platform.system", return_value="Linux"), patch(
        "src.llm.service._terminate_ollama_processes", return_value=1
    ) as terminate, patch("src.llm.service.probe_ollama") as probe, patch(
        "src.llm.service._port_in_use", return_value=False
    ):
        probe.return_value = MagicMock(available=False)
        ok, msg = stop_ollama()
        assert ok
        assert "stop" in msg.lower() or "free" in msg.lower() or "Start" in msg
        assert terminate.called


def test_start_after_stop_retries_spawn():
    """Start must be callable again after stop (no app restart required)."""
    with patch("src.llm.service.OllamaClient") as Cls, patch(
        "src.llm.service.find_ollama_binary", return_value="/usr/bin/ollama"
    ), patch("src.llm.service.subprocess.Popen") as Popen, patch(
        "src.llm.service.time.sleep"
    ), patch("src.llm.service._port_in_use", return_value=False):
        from src.llm.service import start_ollama

        def factory(*a, **k):
            m = MagicMock()

            def list_models():
                if Popen.call_count == 0:
                    raise OllamaError("down")
                return ["qwen3:14b"]

            m.list_models.side_effect = list_models
            return m

        Cls.side_effect = factory
        proc = MagicMock()
        proc.poll.return_value = None
        Popen.return_value = proc
        st = start_ollama(wait_s=5.0)
        assert st.available
        Popen.assert_called()


def test_stop_never_signals_a_process_group_it_does_not_own():
    """
    Regression: a mock left in ``_started_proc`` by an earlier test used to reach
    ``os.killpg(os.getpgid(mock.pid), 15)``. ``MagicMock.__index__`` makes that pid
    1, and ``killpg(1, sig)`` is ``kill(-1, sig)`` — SIGTERM to every process the
    user owns, i.e. an instant desktop logout. Only the held child may be signalled.
    """
    import src.llm.service as svc

    leaked = MagicMock()
    leaked.poll.return_value = None  # looks "still running"
    svc._started_proc = leaked

    with patch("src.llm.service.platform.system", return_value="Linux"), patch(
        "src.llm.service._terminate_ollama_processes", return_value=0
    ), patch("src.llm.service.probe_ollama") as probe, patch(
        "src.llm.service._port_in_use", return_value=False
    ):
        probe.return_value = MagicMock(available=False)
        stop_ollama()  # conftest raises if os.killpg is reached at all

    leaked.terminate.assert_called()


def test_child_pgid_rejects_untrusted_pids():
    from src.llm.service import _child_pgid

    assert _child_pgid(MagicMock()) is None  # mock pid coerces to 1
    for pid in (None, 0, 1, -1, True, "1234"):
        proc = MagicMock()
        proc.pid = pid
        assert _child_pgid(proc) is None, f"pid={pid!r} must not yield a pgid"


def test_is_safe_signal_target_excludes_our_own_processes():
    import os

    from src.llm.service import _is_safe_signal_target, _own_ancestors

    assert not _is_safe_signal_target(1)
    assert not _is_safe_signal_target(os.getpid())
    assert not _is_safe_signal_target(os.getppid())
    # every ancestor (shell, terminal, `systemd --user`) must be off limits.
    # The parent chain is read from /proc, so this only holds on Linux; in a
    # PID-namespace sandbox we are PID 1 and have no ancestors at all.
    ancestors = _own_ancestors()
    if Path("/proc").is_dir() and os.getppid() > 1:
        assert os.getppid() in ancestors
    for pid in ancestors:
        assert not _is_safe_signal_target(pid)


def test_process_patterns_do_not_match_unrelated_command_lines():
    """The old bare-"ollama" pattern matched the test runner itself."""
    import re

    from src.llm.service import _OLLAMA_CMDLINE_PATTERNS

    def matches(cmdline: str) -> bool:
        return any(re.search(p, cmdline) for p in _OLLAMA_CMDLINE_PATTERNS)

    assert matches("/usr/local/bin/ollama serve")
    assert matches("/usr/bin/ollama runner --model qwen3")
    assert matches("/usr/lib/ollama/ollama_llama_server --port 8080")

    assert not matches("python -m pytest tests/test_ollama_service.py")
    assert not matches("/usr/lib/systemd/systemd --user")
    assert not matches("nvim src/llm/ollama_client.py")
    assert not matches("/usr/bin/kwin_wayland --xwayland")


def test_unload_client_sends_keep_alive_zero():
    import json
    from unittest.mock import patch

    from src.llm.ollama_client import OllamaClient

    captured = {}

    def fake_urlopen(req, timeout=None):
        body = req.data
        if body:
            captured["json"] = json.loads(body.decode())
        mock = MagicMock()
        mock.read.return_value = b"{}"
        mock.__enter__.return_value = mock
        mock.__exit__.return_value = False
        return mock

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        OllamaClient().unload_model("gemma4:latest")
    assert captured["json"]["keep_alive"] == 0
    assert captured["json"]["model"] == "gemma4:latest"
