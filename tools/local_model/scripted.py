"""A stand-in for the model client in tests: answers from a fixed text, a list or a function."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

Reply = str | Sequence[str] | Callable[[Sequence[dict[str, Any]], int], str]


class ScriptedClient:
    """Answers each draft or rewrite call from ``reply`` and labels sentences for the advice check.

    ``reply`` is one text, a list used in call order (the last one repeats), or a function
    of the messages and the 0-based index of the call. A sentence holding one of
    ``advice_sentences`` is labeled advice; every other sentence is labeled description.
    ``seed`` is what the runner passed for this repeat, kept so tests can read it back.
    """

    def __init__(self, reply: Reply, *, advice_sentences: Sequence[str] = (), seed: int | None = None) -> None:
        self.reply = reply
        self.advice_sentences = tuple(advice_sentences)
        self.seed = seed
        self.draft_calls: list[list[dict[str, Any]]] = []
        self.structured_calls = 0

    def list_models(self) -> list[str]:
        return ["scripted-model"]

    def chat_turn(self, model: str, messages: Any, **kwargs: Any) -> Any:
        from src.llm.ollama_client import ChatResult

        if kwargs.get("format"):
            self.structured_calls += 1
            return ChatResult(content=json.dumps(self._labels(messages[-1]["content"], kwargs["format"])))
        index = len(self.draft_calls)
        self.draft_calls.append([dict(m) for m in messages])
        if callable(self.reply):
            return ChatResult(content=self.reply(messages, index))
        if isinstance(self.reply, str):
            return ChatResult(content=self.reply)
        return ChatResult(content=self.reply[min(index, len(self.reply) - 1)])

    def _labels(self, text: str, schema: dict[str, Any]) -> dict[str, str]:
        labels = dict.fromkeys(schema["required"], "description")
        for line in str(text).splitlines():
            number, _, sentence = line.partition(". ")
            if number.isdigit() and any(marker in sentence for marker in self.advice_sentences):
                labels[number] = "advice"
        return labels
