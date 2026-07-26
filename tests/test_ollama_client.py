"""Ollama client unit tests with mocked HTTP."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from src.llm.ollama_client import ChatMessage, OllamaClient, OllamaError


def test_list_models_parses_tags():
    payload = {
        "models": [
            {"name": "gemma4:latest"},
            {"name": "qwen2.5:14b"},
        ]
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(payload).encode()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    with patch("urllib.request.urlopen", return_value=mock_resp):
        names = OllamaClient().list_models()
    assert names == ["gemma4:latest", "qwen2.5:14b"]


def test_chat_returns_content():
    payload = {"message": {"role": "assistant", "content": "Sevoflurane was used."}}
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(payload).encode()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    with patch("urllib.request.urlopen", return_value=mock_resp):
        text = OllamaClient().chat(
            "gemma4:latest",
            [ChatMessage("user", "What agent?")],
            stream=False,
        )
    assert "Sevoflurane" in text


def test_unavailable_raises():
    import urllib.error

    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("down"),
    ):
        with pytest.raises(OllamaError, match="ollama serve"):
            OllamaClient().list_models()


def test_chat_stream_concatenates():
    lines = [
        json.dumps({"message": {"content": "Hello "}}) + "\n",
        json.dumps({"message": {"content": "world"}, "done": True}) + "\n",
    ]
    mock_resp = MagicMock()
    mock_resp.__iter__ = lambda self: iter(x.encode() for x in lines)
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    with patch("urllib.request.urlopen", return_value=mock_resp):
        text = OllamaClient().chat(
            "m",
            [{"role": "user", "content": "hi"}],
            stream=True,
        )
    assert text == "Hello world"
