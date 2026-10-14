"""
Ask the local chat model which answer sentences give clinical advice.

``find_directive_phrases`` is the fast first pass, but hedged and indirect advice
("seems like a good idea", "the best course of action") slips past any word list.
The chat model labels every sentence in one call. When that call fails the answer
still ships, checked by the patterns alone.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from src.llm.ollama_client import OllamaClient
from src.llm.prompts import ADVICE_CHECK_SYSTEM, advice_check_message
from src.llm.schemas import strip_think

Label = Literal["description", "advice"]
_LABEL_OF: dict[str, Label] = {"description": "description", "advice": "advice"}

# A warm 9B model labels a six-sentence answer in about a second; the timeout only
# stops a stuck or cold model from holding the answer back.
TIMEOUT_S = 20.0
# `"12": "description",` is about 8 tokens; the margin covers whitespace.
_TOKENS_PER_LABEL = 12


@dataclass(frozen=True)
class Labeled:
    """The model labeled every sentence of the answer, in order."""

    labeled: tuple[tuple[str, Label], ...]

    @property
    def advice(self) -> tuple[str, ...]:
        return tuple(s for s, label in self.labeled if label == "advice")


@dataclass(frozen=True)
class NotRun:
    """The check could not run; only the pattern check applies to the answer."""

    reason: str

    @property
    def advice(self) -> tuple[str, ...]:
        return ()


AdviceCheck = Labeled | NotRun


_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
_MARKUP = re.compile(r"^(?:[#>*\-•]+|\d+[.)])\s*|\*\*")


def split_sentences(text: str) -> list[str]:
    """Answer sentences without list or heading markup; rules and blank lines drop out."""
    pieces = (_MARKUP.sub("", piece).strip() for piece in _BREAK.split(text or ""))
    return [p for p in pieces if re.search(r"[A-Za-z]", p)]


def check_advice(
    text: str, client: OllamaClient, model: str, *, timeout_s: float = TIMEOUT_S
) -> AdviceCheck:
    """Label each sentence of ``text`` in one model call. Never raises."""
    if not model:
        return NotRun("no chat model")
    return label_sentences(split_sentences(text), client, model, timeout_s=timeout_s)


def label_sentences(
    sentences: Sequence[str],
    client: OllamaClient,
    model: str,
    *,
    timeout_s: float = TIMEOUT_S,
) -> AdviceCheck:
    if not sentences:
        return Labeled(())
    keys = [str(i) for i in range(1, len(sentences) + 1)]
    schema = {
        "type": "object",
        "properties": {k: {"type": "string", "enum": list(_LABEL_OF)} for k in keys},
        "required": keys,
    }
    try:
        result = client.chat_turn(
            model,
            [
                {"role": "system", "content": ADVICE_CHECK_SYSTEM},
                {"role": "user", "content": advice_check_message(sentences)},
            ],
            temperature=0.0,
            stream=False,
            format=schema,
            think=False,
            max_tokens=16 + _TOKENS_PER_LABEL * len(sentences),
            timeout_s=timeout_s,
        )
    except TimeoutError:
        return NotRun(f"no reply within {timeout_s:g} s")
    except Exception as exc:  # the answer must ship whatever the model call does
        return NotRun(f"model call failed: {exc}")
    labels = _parse_labels(strip_think(result.content or ""), keys)
    if labels is None:
        return NotRun("model reply was not one label per sentence")
    return Labeled(tuple(zip(sentences, labels, strict=True)))


def _parse_labels(reply: str, keys: list[str]) -> list[Label] | None:
    try:
        data: Any = json.loads(reply)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    labels: list[Label] = []
    for key in keys:
        label = _LABEL_OF.get(str(data.get(key)))
        if label is None:
            return None
        labels.append(label)
    return labels
