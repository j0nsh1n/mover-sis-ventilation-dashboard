"""Case text must not leave this machine without a deliberate override."""

from __future__ import annotations

import pytest

from src.llm.ollama_client import (
    ALLOW_REMOTE_ENV,
    OllamaClient,
    OllamaError,
    check_local_only,
    is_loopback,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434",
        "http://localhost:11434",
        "http://[::1]:11434",
        "http://127.0.0.5:11434",
        "127.0.0.1:11434",
    ],
)
def test_loopback_hosts_are_allowed(url):
    assert is_loopback(url)
    check_local_only(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.50:11434",
        "http://ollama.example.com:11434",
        "https://some-cloud-host/api",
        "http://10.0.0.2:11434",
    ],
)
def test_remote_hosts_are_refused(url, monkeypatch):
    monkeypatch.delenv(ALLOW_REMOTE_ENV, raising=False)
    assert not is_loopback(url)
    with pytest.raises(OllamaError) as e:
        check_local_only(url)
    assert "data use agreement" in str(e.value)


def test_client_refuses_remote_at_construction(monkeypatch):
    monkeypatch.delenv(ALLOW_REMOTE_ENV, raising=False)
    with pytest.raises(OllamaError):
        OllamaClient(base_url="http://192.168.1.50:11434")


def test_explicit_override_permits_remote(monkeypatch):
    monkeypatch.setenv(ALLOW_REMOTE_ENV, "1")
    check_local_only("http://192.168.1.50:11434")
    assert OllamaClient(base_url="http://192.168.1.50:11434").base_url.endswith(":11434")


def test_override_must_be_affirmative(monkeypatch):
    monkeypatch.setenv(ALLOW_REMOTE_ENV, "0")
    with pytest.raises(OllamaError):
        check_local_only("http://192.168.1.50:11434")
