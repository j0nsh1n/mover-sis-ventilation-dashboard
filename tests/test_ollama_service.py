"""Ollama lifecycle helper tests (mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.llm.ollama_client import OllamaError
from src.llm.service import ensure_ollama, find_ollama_binary, ollama_status_summary


def test_find_ollama_binary_env(tmp_path, monkeypatch):
    fake = tmp_path / "ollama"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("OLLAMA_PATH", str(fake))
    assert find_ollama_binary() == str(fake)


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
        client = MagicMock()
        # First constructor uses for initial check — raise
        # Subsequent list_models after start succeed via side_effect on class
        call_count = {"n": 0}

        def client_factory(*a, **k):
            m = MagicMock()

            def list_models():
                call_count["n"] += 1
                if call_count["n"] == 1:
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
