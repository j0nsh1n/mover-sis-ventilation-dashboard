"""Minimal Ollama HTTP client (local LLM)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterator


class OllamaError(RuntimeError):
    """Raised when Ollama is unreachable or returns an error."""


@dataclass
class ChatMessage:
    role: str  # system | user | assistant
    content: str


class OllamaClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        timeout_s: float = 180.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def is_available(self) -> bool:
        try:
            self.list_models()
            return True
        except OllamaError:
            return False

    def list_models(self) -> list[str]:
        data = self._request_json("GET", "/api/tags")
        models = data.get("models") or []
        names = []
        for m in models:
            name = m.get("name") or m.get("model")
            if name:
                names.append(str(name))
        return sorted(names)

    def chat(
        self,
        model: str,
        messages: list[ChatMessage] | list[dict[str, str]],
        *,
        temperature: float = 0.2,
        stream: bool = False,
    ) -> str:
        payload_messages = []
        for m in messages:
            if isinstance(m, ChatMessage):
                payload_messages.append({"role": m.role, "content": m.content})
            else:
                payload_messages.append(
                    {"role": m["role"], "content": m["content"]}
                )
        body = {
            "model": model,
            "messages": payload_messages,
            "stream": stream,
            "options": {"temperature": temperature},
        }
        if stream:
            chunks: list[str] = []
            for piece in self.chat_stream(model, payload_messages, temperature=temperature):
                chunks.append(piece)
            return "".join(chunks)

        data = self._request_json("POST", "/api/chat", body)
        msg = data.get("message") or {}
        content = msg.get("content")
        if not content:
            raise OllamaError(f"Empty response from model {model!r}: {data!r}")
        return str(content)

    def chat_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
    ) -> Iterator[str]:
        body = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": temperature},
        }
        req = urllib.request.Request(
            self.base_url + "/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                for raw in resp:
                    line = raw.decode("utf-8").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("error"):
                        raise OllamaError(str(obj["error"]))
                    msg = obj.get("message") or {}
                    piece = msg.get("content")
                    if piece:
                        yield str(piece)
                    if obj.get("done"):
                        break
        except urllib.error.URLError as e:
            raise OllamaError(
                f"Cannot reach Ollama at {self.base_url}. "
                f"Start it with: ollama serve. ({e})"
            ) from e

    def _request_json(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"} if body else {},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.URLError as e:
            raise OllamaError(
                f"Cannot reach Ollama at {self.base_url}. "
                f"Start it with: ollama serve. ({e})"
            ) from e
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError as e:
            raise OllamaError(f"Invalid JSON from Ollama: {raw[:200]}") from e
