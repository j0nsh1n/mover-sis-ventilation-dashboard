"""Ollama client unit tests with mocked HTTP."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.llm.ollama_client import (
    ChatMessage,
    OllamaClient,
    OllamaEmbedder,
    OllamaError,
    parse_embed_response,
)


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


def test_parse_embed_response_reads_batch_and_legacy_single():
    batch = parse_embed_response({"embeddings": [[0.1, 0.2], [0.3, 0.4]]})
    assert batch == [[0.1, 0.2], [0.3, 0.4]]
    single = parse_embed_response({"embedding": [1.0, 0.0]})
    assert single == [[1.0, 0.0]]
    with pytest.raises(OllamaError, match="no embeddings"):
        parse_embed_response({"model": "x"})


def test_embed_posts_to_embed_endpoint():
    payload = {"embeddings": [[0.5, 0.25]]}
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(payload).encode()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    with patch("urllib.request.urlopen", return_value=mock_resp) as opener:
        vectors = OllamaClient().embed("nomic-embed-text", ["hello"])
    assert vectors == [[0.5, 0.25]]
    req = opener.call_args[0][0]
    assert req.full_url.endswith("/api/embed")


def test_embedder_unavailable_when_model_missing():
    payload = {"models": [{"name": "gemma4:latest"}]}
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(payload).encode()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False
    with patch("urllib.request.urlopen", return_value=mock_resp):
        assert OllamaEmbedder("nomic-embed-text").is_available() is False


def test_embedder_construction_refuses_remote(monkeypatch):
    monkeypatch.delenv("MOVER_ALLOW_REMOTE_OLLAMA", raising=False)
    with pytest.raises(OllamaError):
        OllamaEmbedder("nomic-embed-text", client=OllamaClient(base_url="http://10.0.0.2:11434"))
