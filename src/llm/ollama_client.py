"""Minimal Ollama HTTP client with native tool-calling support."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterator


class OllamaError(RuntimeError):
    """Raised when Ollama is unreachable or returns an error."""


@dataclass
class ChatMessage:
    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class ChatResult:
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] | None = None


class OllamaClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        timeout_s: float = 300.0,
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

    def unload_model(self, model: str) -> None:
        """
        Free VRAM/RAM for ``model`` while leaving the server up.

        Ollama honors ``keep_alive: 0`` on /api/generate (and chat) as an
        immediate unload — same approach as Daily Scheduler.
        """
        body = {
            "model": model,
            "prompt": "",
            "keep_alive": 0,
            "stream": False,
        }
        self._request_json("POST", "/api/generate", body)

    def chat(
        self,
        model: str,
        messages: list[ChatMessage] | list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        """Backward-compatible: returns assistant text content only."""
        result = self.chat_turn(
            model, messages, temperature=temperature, stream=stream, tools=tools
        )
        if not result.content and result.tool_calls:
            return ""  # caller should use chat_turn for tools
        if not result.content:
            raise OllamaError(f"Empty response from model {model!r}")
        return result.content

    def chat_turn(
        self,
        model: str,
        messages: list[ChatMessage] | list[dict[str, Any]],
        *,
        temperature: float = 0.15,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        payload_messages = self._normalize_messages(messages)
        body: dict[str, Any] = {
            "model": model,
            "messages": payload_messages,
            "stream": bool(stream),
            "options": {"temperature": temperature},
        }
        if tools:
            body["tools"] = tools

        if stream:
            return self._chat_stream_collect(model, body)

        data = self._request_json("POST", "/api/chat", body)
        msg = data.get("message") or {}
        content = str(msg.get("content") or "")
        calls = msg.get("tool_calls") or []
        if not isinstance(calls, list):
            calls = []
        return ChatResult(content=content, tool_calls=list(calls), raw=data)

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
        for piece, _calls, done in self._iter_chat_stream(body):
            if piece:
                yield piece
            if done:
                break

    def _chat_stream_collect(self, model: str, body: dict[str, Any]) -> ChatResult:
        content_parts: list[str] = []
        calls: list[dict[str, Any]] = []
        for piece, tc, done in self._iter_chat_stream(body):
            if piece:
                content_parts.append(piece)
            if tc:
                calls.extend(tc)
            if done:
                break
        return ChatResult(content="".join(content_parts), tool_calls=calls)

    def _iter_chat_stream(
        self, body: dict[str, Any]
    ) -> Iterator[tuple[str, list[dict[str, Any]], bool]]:
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
                    piece = msg.get("content") or ""
                    tcs = msg.get("tool_calls") or []
                    if not isinstance(tcs, list):
                        tcs = []
                    yield str(piece), list(tcs), bool(obj.get("done"))
        except urllib.error.URLError as e:
            raise OllamaError(
                f"Cannot reach Ollama at {self.base_url}. "
                f"Start it with: ollama serve. ({e})"
            ) from e

    def _normalize_messages(
        self, messages: list[ChatMessage] | list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if isinstance(m, ChatMessage):
                d: dict[str, Any] = {"role": m.role, "content": m.content or ""}
                if m.tool_calls:
                    d["tool_calls"] = m.tool_calls
                if m.tool_call_id:
                    d["tool_call_id"] = m.tool_call_id
                if m.name:
                    d["name"] = m.name
                out.append(d)
            else:
                out.append(dict(m))
        return out

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
